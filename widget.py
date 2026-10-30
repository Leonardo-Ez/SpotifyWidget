"""
Widget "Now Playing" de Spotify incrustado en la parte izquierda de la barra de tareas.

Lee la canción actual desde el control multimedia de Windows (SMTC), así que no
necesita cuenta de desarrollador ni API de Spotify. La ventana se mete como hija
de la barra (Shell_TrayWnd), así que se mueve y se oculta junto con ella y nunca
queda tapada.

Controles:
  - Clic izquierdo:  play / pausa
  - Rueda del ratón: siguiente / anterior
  - Clic derecho:    menú (anterior, siguiente, abrir Spotify, salir)
"""

import asyncio
import ctypes
import math
import os
import random
import subprocess
import sys
import threading
import winreg
from ctypes import wintypes

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication, QIcon,
                           QImage, QPainter, QPainterPath, QPalette, QPixmap, QWindow)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)
from winrt.windows.storage.streams import DataReader

# ---------------------------------------------------------------- configuración
OFFSET_X = 12            # distancia desde el borde izquierdo de la barra
MAX_TEXT_WIDTH = 180     # ancho máximo del título/artista antes de recortar con "…"
SOLO_SPOTIFY = True      # False = muestra cualquier reproductor (navegador, etc.)
POLL_SECONDS = 0.5       # cada cuánto se consulta el estado de reproducción
N_BARS = 9               # barras altas de la onda
N_DOTS = 3               # puntitos a cada lado de la onda

# ---------------------------------------------------------------- Win32
user32 = ctypes.windll.user32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# sin argtypes ctypes pasa los HWND como int de 32 bits y en x64 llegan corruptos
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.FindWindowW.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
HWND_TOP = wintypes.HWND(0)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

single_instance_mutex = None


def find_taskbar():
    return user32.FindWindowW("Shell_TrayWnd", None)


def system_uses_light_theme() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def fluent_icon(glyph: str, color: QColor, dpr: float, size: int = 16) -> QIcon:
    """Icono de la fuente Segoe Fluent Icons (la que usa Windows 11 en sus menús)."""
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.TextAntialiasing)
    font = QFont("Segoe Fluent Icons")
    font.setPixelSize(size - 4)
    p.setFont(font)
    p.setPen(color)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph)
    p.end()
    return QIcon(pix)


def restart_self():
    """Explorer se reinició y se llevó la barra: relanzar el widget desde cero."""
    kernel32.CloseHandle(single_instance_mutex)
    if getattr(sys, "frozen", False):   # compilado con PyInstaller: el .exe es el programa
        subprocess.Popen([sys.executable] + sys.argv[1:])
    else:
        subprocess.Popen([sys.executable] + sys.argv)
    os._exit(0)


# ---------------------------------------------------------------- lectura del SMTC
class MediaWatcher(QObject):
    """Corre un loop asyncio en otro hilo y emite cambios hacia la interfaz."""

    track_changed = Signal(str, str, bytes)   # título, artista, portada (png/jpg)
    status_changed = Signal(bool)             # está sonando
    session_lost = Signal()

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.session = None
        self._last_key = None
        self._last_playing = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._poll())

    def _pick_session(self, manager):
        sessions = list(manager.get_sessions())
        spotify = [s for s in sessions if "spotify" in s.source_app_user_model_id.lower()]
        if spotify:
            return spotify[0]
        if not SOLO_SPOTIFY:
            return manager.get_current_session()
        return None

    async def _poll(self):
        manager = await SessionManager.request_async()
        while True:
            try:
                await self._tick(manager)
            except Exception as e:  # el SMTC a veces falla al cambiar de canción
                print("SMTC:", e, file=sys.stderr)
            await asyncio.sleep(POLL_SECONDS)

    async def _tick(self, manager):
        session = self._pick_session(manager)
        self.session = session
        if session is None:
            if self._last_key is not None:
                self._last_key = self._last_playing = None
                self.session_lost.emit()
            return

        props = await session.try_get_media_properties_async()
        key = (props.title, props.artist)
        if key != self._last_key and props.title:
            self._last_key = key
            cover = await self._read_thumbnail(props.thumbnail)
            self.track_changed.emit(props.title or "", props.artist or "", cover)

        playing = session.get_playback_info().playback_status == PlaybackStatus.PLAYING
        if playing != self._last_playing:
            self._last_playing = playing
            self.status_changed.emit(playing)

    @staticmethod
    async def _read_thumbnail(ref) -> bytes:
        if ref is None:
            return b""
        stream = await ref.open_read_async()
        size = stream.size
        reader = DataReader(stream)
        await reader.load_async(size)
        data = bytearray(size)
        reader.read_bytes(data)
        return bytes(data)

    # comandos (se llaman desde el hilo de Qt)
    def _send(self, name):
        s = self.session
        if s is not None:
            asyncio.run_coroutine_threadsafe(getattr(s, name)(), self.loop)

    def toggle(self):   self._send("try_toggle_play_pause_async")
    def next(self):     self._send("try_skip_next_async")
    def previous(self): self._send("try_skip_previous_async")


# ---------------------------------------------------------------- ventana
class NowPlayingWidget(QWidget):
    def __init__(self, media: MediaWatcher):
        super().__init__(None, Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self.media = media
        self.title, self.artist = "", ""
        self.cover = QPixmap()
        self.playing = False
        self.hover = False

        light = system_uses_light_theme()
        self.c_title = QColor("#1b1b1b") if light else QColor("#ffffff")
        self.c_artist = QColor("#5c5c5c") if light else QColor("#c5c5c5")
        self.c_hover = QColor(0, 0, 0, 18) if light else QColor(255, 255, 255, 20)
        self.c_wave = self.c_title

        self.f_title = QFont("Segoe UI Variable Text", 10, QFont.DemiBold)
        self.f_artist = QFont("Segoe UI Variable Text", 9)

        # onda: altura actual y objetivo de cada barra (0..1)
        self.levels = [0.0] * N_BARS
        self.targets = [0.0] * N_BARS
        self.phase = 0.0

        self.taskbar = None
        self.embed()

        media.track_changed.connect(self.on_track)
        media.status_changed.connect(self.on_status)
        media.session_lost.connect(self.on_lost)

        self.anim = QTimer(self, interval=33, timeout=self.animate)
        self.anim.start()
        self.watchdog = QTimer(self, interval=1000, timeout=self.check_taskbar)
        self.watchdog.start()

    # ---------- incrustar en la barra
    def embed(self):
        self.taskbar = find_taskbar()
        self.winId()
        self.taskbar_window = QWindow.fromWinId(self.taskbar)  # hay que mantenerla viva
        self.windowHandle().setParent(self.taskbar_window)
        self.relayout()
        # por encima de los demás hijos de la barra (el contenido XAML de Windows 11)
        user32.SetWindowPos(int(self.winId()), HWND_TOP, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def check_taskbar(self):
        if not user32.IsWindow(self.taskbar) or find_taskbar() != self.taskbar:
            restart_self()

    def taskbar_height(self):
        screen = QGuiApplication.primaryScreen()
        h = screen.geometry().bottom() - screen.availableGeometry().bottom()
        return h if h > 0 else 48   # barra oculta automáticamente

    def relayout(self):
        h = self.taskbar_height()
        self.thumb = h - 16
        tw = max(QFontMetrics(self.f_title).horizontalAdvance(self.title),
                 QFontMetrics(self.f_artist).horizontalAdvance(self.artist))
        self.text_w = min(tw, MAX_TEXT_WIDTH)
        self.wave_w = (N_DOTS * 2 + N_BARS) * 5
        w = 8 + self.thumb + 10 + self.text_w + 16 + self.wave_w + 10
        self.setGeometry(OFFSET_X, 0, w, h)   # coordenadas relativas a la barra
        self.update()

    # ---------- señales del SMTC
    def on_track(self, title, artist, cover):
        self.title, self.artist = title, artist
        img = QImage.fromData(cover) if cover else QImage()
        self.cover = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        self.relayout()
        self.show()

    def on_status(self, playing):
        self.playing = playing
        self.update()

    def on_lost(self):
        self.title = ""
        self.hide()

    # ---------- animación
    def animate(self):
        if not self.isVisible():
            return
        self.phase += 0.12
        for i in range(N_BARS):
            if self.playing:
                # envolvente: más alto al centro, con ruido para que "baile"
                center = 1 - abs(i - (N_BARS - 1) / 2) / (N_BARS / 2)
                if random.random() < 0.25:
                    self.targets[i] = 0.25 + 0.75 * random.random() * (0.45 + 0.55 * center)
                wobble = 0.08 * math.sin(self.phase + i * 0.9)
                goal = min(1.0, max(0.0, self.targets[i] + wobble))
            else:
                goal = 0.0
            self.levels[i] += (goal - self.levels[i]) * 0.25
        self.update()

    # ---------- dibujo
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        h = self.height()

        # alfa mínimo en todo el rectángulo para que reciba clics
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        if self.hover:
            p.setPen(Qt.NoPen)
            p.setBrush(self.c_hover)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 4, -2, -4), 6, 6)

        # portada
        tx, ty = 8, (h - self.thumb) / 2
        path = QPainterPath()
        path.addRoundedRect(QRectF(tx, ty, self.thumb, self.thumb), 4, 4)
        if not self.cover.isNull():
            p.save()
            p.setClipPath(path)
            p.drawPixmap(QRectF(tx, ty, self.thumb, self.thumb), self.cover,
                         QRectF(self.cover.rect()))
            p.restore()
        else:
            p.fillPath(path, QColor(128, 128, 128, 90))

        # texto
        x = tx + self.thumb + 10
        fm_t, fm_a = QFontMetrics(self.f_title), QFontMetrics(self.f_artist)
        total = fm_t.height() + fm_a.height() - 2
        y = (h - total) / 2
        p.setFont(self.f_title)
        p.setPen(self.c_title)
        p.drawText(QPoint(int(x), int(y + fm_t.ascent())),
                   fm_t.elidedText(self.title, Qt.ElideRight, self.text_w))
        p.setFont(self.f_artist)
        p.setPen(self.c_artist)
        p.drawText(QPoint(int(x), int(y + fm_t.height() - 2 + fm_a.ascent())),
                   fm_a.elidedText(self.artist, Qt.ElideRight, self.text_w))

        # onda  ···ıllıllı···
        wx = x + self.text_w + 16
        cy = h / 2
        p.setPen(Qt.NoPen)
        p.setBrush(self.c_wave)
        bar_w, step, max_h = 2.6, 5, h * 0.42
        items = [None] * N_DOTS + list(range(N_BARS)) + [None] * N_DOTS
        for k, idx in enumerate(items):
            bx = wx + k * step
            bh = bar_w if idx is None else max(bar_w, self.levels[idx] * max_h)
            p.drawRoundedRect(QRectF(bx, cy - bh / 2, bar_w, bh), bar_w / 2, bar_w / 2)

    # ---------- ratón
    def enterEvent(self, _):
        self.hover = True
        self.update()

    def leaveEvent(self, _):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.media.toggle()

    def wheelEvent(self, e):
        (self.media.next if e.angleDelta().y() < 0 else self.media.previous)()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        color = menu.palette().color(QPalette.WindowText)
        dpr = self.devicePixelRatioF()
        menu.addAction(QAction(fluent_icon("", color, dpr), "Anterior", menu,
                               triggered=self.media.previous))
        menu.addAction(QAction(fluent_icon("", color, dpr), "Siguiente", menu,
                               triggered=self.media.next))
        menu.addSeparator()
        menu.addAction(QAction("Abrir Spotify", menu,
                               triggered=lambda: ctypes.windll.shell32.ShellExecuteW(
                                   None, "open", "spotify:", None, None, 1)))
        menu.addAction(QAction("Salir", menu, triggered=self.quit))
        menu.exec(e.globalPos())

    def quit(self):
        # soltar la barra antes de salir; si Qt destruye la ventana mientras sigue
        # siendo hija de Explorer, el cierre se queda colgado
        self.hide()
        user32.SetParent(int(self.winId()), None)
        os._exit(0)


def main():
    global single_instance_mutex
    # una sola instancia: si ya hay un widget abierto, salir
    single_instance_mutex = kernel32.CreateMutexW(None, False, "SpotifyTaskbarWidget")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    media = MediaWatcher()
    widget = NowPlayingWidget(media)
    QGuiApplication.primaryScreen().availableGeometryChanged.connect(lambda _: widget.relayout())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
