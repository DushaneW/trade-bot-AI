@echo off
rem Quorum installer for Windows. Double-click this file.
rem Creates a private Python environment in .venv and installs everything Quorum needs.
setlocal
cd /d "%~dp0"

echo.
echo  Installing Quorum
echo.

set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Quorum needs Python 3.10 or newer, and I couldn't find it.
  echo  Install it from https://www.python.org/downloads/windows/
  echo  and tick "Add python.exe to PATH" during setup. Then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo  Couldn't create the environment. Try moving the folder somewhere with a shorter path, like C:\quorum
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo  Installing the packages failed. Check your internet connection and run this file again.
  pause
  exit /b 1
)

echo.
echo  Done. Double-click "Start Quorum.bat" to start it.
echo.
pause
