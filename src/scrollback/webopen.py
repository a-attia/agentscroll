"""Open a URL in a browser, preferring a standalone *window* over a tab.

All platform detection lives here in Python (not baked into per-OS shell
scripts), so the behaviour is consistent and the launchers stay dumb.

Strategy, in order of preference:
1. A Chromium-family browser in "app mode" (`--app=<url>`), which gives a
   chromeless standalone window that reads like a native app. We discover
   the browser executable portably (PATH + per-OS default locations).
2. The stdlib `webbrowser` module (opens a tab in the default browser).

Nothing here is required for normal operation; it is best-effort and
falls back gracefully.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

# Executable names to look for on PATH (all platforms).
_CHROMIUM_BINARIES = (
    "chromium", "chromium-browser", "chrome", "google-chrome",
    "google-chrome-stable", "brave", "brave-browser", "microsoft-edge",
    "msedge", "vivaldi",
)

# Per-OS default install locations, checked when PATH lookup fails. Kept as
# data (not control flow) so adding a platform/browser is a one-line change.
_CHROMIUM_PATHS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
    # Linux/BSD rely on PATH lookup above; common names are covered there.
}


def _find_chromium() -> str | None:
    """Return a path to a Chromium-family browser, or None if not found."""
    for name in _CHROMIUM_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in _CHROMIUM_PATHS.get(sys.platform, []):
        if Path(candidate).exists():
            return candidate
    return None


def open_window(url: str) -> str:
    """Open `url`, preferring a standalone window. Returns the method used.

    Return values: "app" (chromeless app window), "tab" (default browser
    tab), or "failed".
    """
    chromium = _find_chromium()
    if chromium:
        try:
            # `--app=<url>` => standalone window with no tab strip / omnibox.
            # A per-app profile dir keeps it isolated from normal browsing and
            # makes the window open reliably even if the browser is running.
            profile = Path(
                os.environ.get("SCROLLBACK_BROWSER_PROFILE")
                or (Path.home() / ".cache" / "scrollback" / "browser")
            )
            profile.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(
                [chromium, f"--app={url}", f"--user-data-dir={profile}", "--new-window"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return "app"
        except OSError:
            pass  # fall through to the default browser

    # Fallback: stdlib webbrowser (a tab in the default browser).
    try:
        if webbrowser.open(url):
            return "tab"
    except webbrowser.Error:
        pass
    return "failed"
