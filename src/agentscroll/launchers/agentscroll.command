#!/bin/bash
# Double-clickable launcher for macOS (Finder: double-click this file).
# Starts the agentscroll web app and opens it in your default browser.
#
# Install a copy you can double-click with:  agentscroll install-launcher
# First-time setup: right-click -> Open (to bypass Gatekeeper once).
#
# If `agentscroll` is not on PATH, this falls back to `python3 -m agentscroll`.

if command -v agentscroll >/dev/null 2>&1; then
  exec agentscroll web
elif python3 -c "import agentscroll" >/dev/null 2>&1; then
  exec python3 -m agentscroll.cli web
else
  echo "agentscroll is not installed."
  echo "Install it with:  pip install \"agentscroll[web]\""
  echo
  read -r -p "Press Return to close."
fi
