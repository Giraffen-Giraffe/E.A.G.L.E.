@echo off
rem E.A.G.L.E. web server edition (shared on the LAN). Stop with Ctrl+C
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup.ps1 -Server first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m eagle server %*
