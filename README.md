# Spotify Taskbar Widget

Widget "Now Playing" para Windows 11 que muestra la canción de Spotify (portada, título, artista y una onda animada) en la parte izquierda de la barra de tareas.

- Lee la reproducción desde el control multimedia de Windows (SMTC): no necesita API de Spotify ni iniciar sesión.
- Se incrusta como ventana hija de la barra de tareas, así que nunca queda tapado y se oculta junto con ella.
- Consumo: ~0,1 % de CPU y ~32 MB de RAM mientras suena.

## Controles

| Acción | Efecto |
|---|---|
| Clic izquierdo | Play / pausa |
| Rueda del ratón | Siguiente / anterior |
| Clic derecho | Menú: anterior, siguiente, abrir Spotify, salir |

## Instalación

Requiere Python 3.10+ en Windows 10/11.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Uso

Ejecuta `iniciar.bat` (abre el widget sin ventana de consola). Aparece cuando Spotify está reproduciendo y se oculta cuando no hay nada sonando.

## Compilar como programa (.exe)

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
build.bat
```

Genera `dist\SpotifyWidget\SpotifyWidget.exe`. Para que arranque con Windows, crea un acceso directo al `.exe` en la carpeta de inicio (`Win + R` → `shell:startup`); después se puede activar o desactivar en *Configuración → Aplicaciones → Inicio*.

## Configuración

Al inicio de `widget.py`:

- `OFFSET_X`: distancia desde el borde izquierdo (útil si tienes el botón de Widgets/clima activado).
- `MAX_TEXT_WIDTH`: ancho máximo del título antes de recortarlo.
- `SOLO_SPOTIFY`: `False` para mostrar cualquier reproductor (navegador, etc.).
