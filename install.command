#!/usr/bin/env bash
# Double-click this file in Finder to install Quorum on a Mac.
cd "$(dirname "$0")"
chmod +x install.sh start.sh "Start Quorum.command" 2>/dev/null
./install.sh
echo
read -n 1 -s -r -p "Press any key to close this window."
