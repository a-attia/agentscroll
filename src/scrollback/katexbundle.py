"""Inline the vendored KaTeX into a self-contained HTML export.

The static HTML export must typeset math **offline** -- a researcher saves or
prints a transcript and the equations have to render with no network and no
sibling asset files. So when an export is rendered in ``math="rendered"``
mode we embed everything KaTeX needs directly in the document:

- the KaTeX stylesheet, with every ``@font-face`` ``url(...)`` rewritten to a
  base64 ``data:`` URI so the fonts travel inside the file;
- the KaTeX JavaScript;
- a small boot script that typesets each ``.math-tex`` placeholder emitted by
  :func:`scrollback.minimd.render`.

The assets are the same files served to the live web app
(``web/static/vendor/katex``); they are read once and cached per process.
"""

from __future__ import annotations

import base64
import functools
import re
from pathlib import Path

_KATEX_DIR = Path(__file__).parent / "web" / "static" / "vendor" / "katex"
_FONT_URL_RE = re.compile(r"url\(fonts/([A-Za-z0-9_.-]+)\.(woff2|woff|ttf)\)")
_FONT_MIME = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf"}


def available() -> bool:
    """True if the vendored KaTeX JS + CSS are present."""
    return (_KATEX_DIR / "katex.min.js").is_file() and (
        _KATEX_DIR / "katex.min.css"
    ).is_file()


@functools.lru_cache(maxsize=1)
def _inlined_css() -> str:
    css = (_KATEX_DIR / "katex.min.css").read_text(encoding="utf-8")
    fonts_dir = _KATEX_DIR / "fonts"

    def _sub(m: re.Match[str]) -> str:
        name, ext = m.group(1), m.group(2)
        path = fonts_dir / f"{name}.{ext}"
        if not path.is_file():
            # Drop references to fonts we do not ship (only woff2 is vendored);
            # browsers fall back to the woff2 face listed alongside.
            return "url()"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"url(data:{_FONT_MIME[ext]};base64,{data})"

    return _FONT_URL_RE.sub(_sub, css)


@functools.lru_cache(maxsize=1)
def _katex_js() -> str:
    return (_KATEX_DIR / "katex.min.js").read_text(encoding="utf-8")


def head_assets() -> str:
    """Return ``<style>`` (KaTeX CSS, fonts inlined) for the document head."""
    if not available():
        return ""
    return f"<style>{_inlined_css()}</style>"


def autorender_script() -> str:
    """Return the ``<script>`` block: KaTeX plus the typeset-on-load boot code."""
    if not available():
        return ""
    boot = (
        "<script>"
        "window.addEventListener('load',function(){"
        "if(typeof katex==='undefined')return;"
        "document.querySelectorAll('.math-tex').forEach(function(n){"
        "try{katex.render(n.textContent,n,{displayMode:n.dataset.display==='true',"
        "throwOnError:false,output:'html'});}catch(e){}"
        "});});"
        "</script>"
    )
    return f"<script>{_katex_js()}</script>\n{boot}"
