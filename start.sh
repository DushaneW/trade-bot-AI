#!/usr/bin/env bash
# Start Quorum on macOS or Linux. Run install.sh once before the first start.
set -euo pipefail
cd "$(dirname "$0")"

venv_python() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe  # Git Bash on Windows
  fi
}

if [ -z "$(venv_python)" ]; then
  echo "Quorum isn't installed yet. Running the installer first."
  bash ./install.sh
fi

exec "$(venv_python)" run.py "$@"
