#!/usr/bin/env bash
# Quorum installer for macOS and Linux.
# Creates a private Python environment in .venv and installs everything Quorum needs.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1;38;5;208m%s\033[0m\n' "$1"; }

say "Installing Quorum"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Quorum needs Python 3.10 or newer, and I couldn't find it."
  if [ "$(uname)" = "Darwin" ]; then
    echo "On a Mac, the easiest way is the installer from https://www.python.org/downloads/macos/"
    echo "or, if you use Homebrew:  brew install python"
  else
    echo "On Ubuntu or Debian:  sudo apt install python3 python3-venv"
  fi
  exit 1
fi
echo "Using $($PYTHON --version)"

if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv || {
    echo "Couldn't create the environment. On Ubuntu or Debian run: sudo apt install python3-venv"
    exit 1
  }
fi

VENV_PY=.venv/bin/python
[ -x .venv/Scripts/python.exe ] && VENV_PY=.venv/Scripts/python.exe  # Git Bash on Windows

"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt

chmod +x start.sh "Start Quorum.command" 2>/dev/null || true

say "Done"
echo "Start Quorum any time with:  ./start.sh"
if [ "$(uname)" = "Darwin" ]; then
  echo "or double-click \"Start Quorum.command\" in Finder."
fi
