#!/bin/bash
# Launcher for Linux / BSD. Make executable (chmod +x agentscroll.sh) and run,
# or wire it into a .desktop entry (see agentscroll.desktop).

if command -v agentscroll >/dev/null 2>&1; then
  exec agentscroll web --window
elif python3 -c "import agentscroll" >/dev/null 2>&1; then
  exec python3 -m agentscroll.cli web --window
else
  echo "agentscroll is not installed. Install with: pip install \"agentscroll[web]\""
  exit 1
fi
