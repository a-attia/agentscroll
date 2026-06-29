"""Cross-platform clipboard copy using only stdlib + OS utilities.

Falls back gracefully: on macOS uses `pbcopy`, on Linux `wl-copy`/`xclip`/
`xsel`, on Windows `clip`. Returns True on success.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _candidates() -> list[list[str]]:
    if sys.platform == "darwin":
        return [["pbcopy"]]
    if sys.platform == "win32":
        return [["clip"]]
    # Linux / BSD: prefer Wayland, then X11 tools.
    return [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]


def copy(text: str) -> bool:
    """Copy `text` to the system clipboard. Returns True if it worked."""
    for cmd in _candidates():
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = subprocess.run(cmd, input=text.encode("utf-8"), check=False)
            if proc.returncode == 0:
                return True
        except OSError:
            continue
    return False
