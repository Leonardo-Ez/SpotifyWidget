@echo off
rem Compila dist\SpotifyWidget\SpotifyWidget.exe
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name SpotifyWidget --icon icon.ico --version-file version_info.txt ^
  --exclude-module tkinter widget.py
