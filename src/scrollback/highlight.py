"""A tiny, dependency-free syntax highlighter for code blocks in exports.

Scope is intentionally narrow: the languages that dominate AI coding
transcripts (bash/shell, python, javascript/jsx/typescript, json), plus a
generic fallback that highlights strings/comments/numbers. The goal is a
*self-contained* HTML export -- colourful code with no JS and no external
dependency, suitable for printing or offline viewing.

Safety: the public `highlight(code, lang)` takes RAW (unescaped) source,
HTML-escapes it, and only ever emits `<span class="hl-...">` wrappers. It
never emits attributes derived from the input, so transcript content
cannot inject markup.
"""

from __future__ import annotations

import html as _html
import re

# Token CSS classes (paired with the palette in CSS_LIGHT / CSS_DARK).
_KW = "hl-kw"
_STR = "hl-str"
_COM = "hl-com"
_NUM = "hl-num"
_FUNC = "hl-fn"

_PY_KEYWORDS = {
    "def", "class", "return", "if", "elif", "else", "for", "while", "try",
    "except", "finally", "with", "as", "import", "from", "in", "not", "and",
    "or", "is", "lambda", "None", "True", "False", "pass", "break", "continue",
    "raise", "yield", "global", "nonlocal", "assert", "del", "async", "await",
}
_JS_KEYWORDS = {
    "function", "return", "if", "else", "for", "while", "do", "switch", "case",
    "break", "continue", "const", "let", "var", "new", "class", "extends",
    "import", "from", "export", "default", "try", "catch", "finally", "throw",
    "typeof", "instanceof", "in", "of", "this", "super", "async", "await",
    "yield", "null", "undefined", "true", "false", "void", "delete",
}
_SH_KEYWORDS = {
    "if", "then", "elif", "else", "fi", "for", "while", "do", "done", "case",
    "esac", "in", "function", "return", "export", "local", "cd", "echo",
    "set", "unset", "source",
}

_LANG_KEYWORDS = {
    "python": _PY_KEYWORDS, "py": _PY_KEYWORDS,
    "javascript": _JS_KEYWORDS, "js": _JS_KEYWORDS, "jsx": _JS_KEYWORDS,
    "typescript": _JS_KEYWORDS, "ts": _JS_KEYWORDS, "tsx": _JS_KEYWORDS,
    "bash": _SH_KEYWORDS, "sh": _SH_KEYWORDS, "shell": _SH_KEYWORDS, "zsh": _SH_KEYWORDS,
}

_COMMENT_PREFIX = {
    "python": "#", "py": "#", "bash": "#", "sh": "#", "shell": "#", "zsh": "#",
    "yaml": "#", "yml": "#", "toml": "#", "ruby": "#", "r": "#",
}

# A single tokenizer pass: strings, comments, numbers, identifiers, other.
_TOKEN = re.compile(
    r"""
    (?P<dstr>"(?:\\.|[^"\\])*") |
    (?P<sstr>'(?:\\.|[^'\\])*') |
    (?P<tstr>`(?:\\.|[^`\\])*`) |
    (?P<num>\b\d+\.?\d*\b) |
    (?P<ident>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<ws>\s+) |
    (?P<other>.)
    """,
    re.VERBOSE,
)


def highlight(code: str, lang: str | None) -> str:
    """Return HTML (escaped) for `code`, with `<span>` highlight wrappers."""
    lang = (lang or "").lower()
    keywords = _LANG_KEYWORDS.get(lang)
    comment_prefix = _COMMENT_PREFIX.get(lang, "#" if keywords is _SH_KEYWORDS else None)

    out: list[str] = []
    for raw_line in code.split("\n"):
        out.append(_highlight_line(raw_line, keywords, lang, comment_prefix))
    return "\n".join(out)


def _highlight_line(line: str, keywords, lang: str, comment_prefix: str | None) -> str:
    # Whole-line comment handling for # / // styles (kept simple + safe).
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if comment_prefix and stripped.startswith(comment_prefix):
        return indent + _wrap(_COM, _html.escape(stripped))
    if lang in ("js", "jsx", "ts", "tsx", "javascript", "typescript") and stripped.startswith("//"):
        return indent + _wrap(_COM, _html.escape(stripped))

    pieces: list[str] = []
    for m in _TOKEN.finditer(line):
        kind = m.lastgroup
        val = m.group()
        if kind in ("dstr", "sstr", "tstr"):
            pieces.append(_wrap(_STR, _html.escape(val)))
        elif kind == "num":
            pieces.append(_wrap(_NUM, _html.escape(val)))
        elif kind == "ident" and keywords and val in keywords:
            pieces.append(_wrap(_KW, _html.escape(val)))
        else:
            pieces.append(_html.escape(val))
    return "".join(pieces)


def _wrap(cls: str, escaped_text: str) -> str:
    return f'<span class="{cls}">{escaped_text}</span>'


# Inlined palette for the export. Theme-aware via prefers-color-scheme so the
# single self-contained file looks right in both light and dark.
HL_CSS = """
.md pre code .hl-kw { color: #cf6cd6; }
.md pre code .hl-str { color: #6aab73; }
.md pre code .hl-com { color: #8a8f99; font-style: italic; }
.md pre code .hl-num { color: #d39a4f; }
.md pre code .hl-fn { color: #4c95d6; }
@media (prefers-color-scheme: light) {
  .md pre code .hl-kw { color: #a626a4; }
  .md pre code .hl-str { color: #50a14f; }
  .md pre code .hl-com { color: #9aa0a6; }
  .md pre code .hl-num { color: #b76b01; }
  .md pre code .hl-fn { color: #4078f2; }
}
"""
