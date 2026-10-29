@echo off
rem E.A.G.L.E. desktop edition (runs only on this PC)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run scripts\setup.ps1 first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m eagle desktop
