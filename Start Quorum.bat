@echo off
rem Double-click to start Quorum on Windows. Keep this window open while Quorum runs.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Quorum isn't installed yet. Running the installer first.
  call install.bat
  if not exist ".venv\Scripts\python.exe" exit /b 1
)

title Quorum
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
