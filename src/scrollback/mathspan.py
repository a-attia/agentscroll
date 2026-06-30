"""Detect and protect delimited-LaTeX math spans in transcript text.

A model often replies with LaTeX -- inline (`$\\nabla\\cdot u = 0$`,
`\\(x^2\\)`) or display (`$$E=mc^2$$`, `\\[\\int_0^1 x\\,dx\\]`). Left to the
Markdown renderer, the `\\`, `_`, `*`, and `^` inside such a span get
mangled (a `_` becomes emphasis, a stray `\\` is dropped, ...), corrupting
the equation. This module finds those spans so the rest of the pipeline can
shield them.

Scope is *delimited* LaTeX only. The three forms math appears in are:
delimited LaTeX (handled here, because it can be detected reliably),
Unicode math (`\u2207\u00b7u`), and plain ASCII (`x^2 + y^2`). The latter two are
deliberately out of scope -- detecting them would mean guessing, with false
positives in ordinary prose and code.

The detection is intentionally conservative:

- Display delimiters (`$$...$$`, `\\[...\\]`) and the escaped-paren inline
  form (`\\(...\\)`) are unambiguous and always recognised.
- The single-`$...$` inline form is recognised only when it does not look
  like ordinary currency/prose: no whitespace directly inside the
  delimiters, no digit immediately after the closing `$` (so `$5` and
  `$3.50` and `it cost $5 to $10` are left alone), and the body is
  non-empty and single-line.
- Nothing inside a fenced/inline code span is treated as math; the caller
  is responsible for not handing code to this module (both `minimd` and the
  browser pass only prose runs / use code placeholders first).

The public surface is small:

- `find_spans(text)` -> list of `Span` (start, end, body, mode, raw).
- `protect(text)` -> `(masked_text, tokens)` where each math span is
  replaced by an opaque placeholder safe to pass through Markdown.
- `restore(html, tokens, render)` -> the placeholders swapped back, each
  rendered by the supplied `render(body, display) -> str` callback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Placeholder wrapper. Uses NUL bytes so it cannot collide with real
# transcript text and so the Markdown renderers treat it as inert inline
# text (no special characters inside).
_PH_OPEN = "\x00MATH"
_PH_CLOSE = "\x00"
_PH_RE = re.compile(r"\x00MATH(\d+)\x00")


@dataclass(frozen=True)
class Span:
    """A detected math span within the source text."""

    start: int  # index of the first delimiter char
    end: int  # index just past the last delimiter char
    body: str  # the LaTeX between the delimiters (delimiters stripped)
    display: bool  # True for display math ($$ / \[), False for inline
    raw: str  # the full matched text including delimiters


# Ordered by precedence: longer / unambiguous delimiters first so e.g. `$$`
# is consumed before the single-`$` rule ever sees it.
#
# Each entry is (compiled_regex, display, body_group). The regexes are
# matched against the *whole* text with finditer; overlap is prevented by
# scanning left to right and skipping matches that start inside an already
# claimed span (see find_spans).
_DISPLAY_DOLLAR = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_DISPLAY_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_INLINE_PAREN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
# Single-dollar inline: no whitespace just inside, single line, non-empty,
# not immediately followed by a digit (currency guard). The body may not
# contain a `$`.
_INLINE_DOLLAR = re.compile(r"\$(?!\s)([^$\n]*[^$\s])\$(?![\d])")

_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (_DISPLAY_DOLLAR, True),
    (_DISPLAY_BRACKET, True),
    (_INLINE_PAREN, False),
    (_INLINE_DOLLAR, False),
)

# Code regions, which must never be treated as math. Fenced blocks first so
# their (possibly `$`-containing) bodies are claimed before inline spans.
_FENCE_BLOCK = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,}).*?\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.DOTALL)
_INLINE_CODE = re.compile(r"(`+)(?:.+?)\1")


def _code_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for m in _FENCE_BLOCK.finditer(text):
        ranges.append((m.start(), m.end()))

    def _in_fence(pos: int) -> bool:
        return any(a <= pos < b for a, b in ranges)

    for m in _INLINE_CODE.finditer(text):
        if not _in_fence(m.start()):
            ranges.append((m.start(), m.end()))
    return ranges


def find_spans(text: str) -> list[Span]:
    """Return the non-overlapping math spans in `text`, left to right.

    Spans overlapping a fenced or inline code region are excluded -- code is
    never math.
    """
    code = _code_ranges(text)

    def _in_code(start: int, end: int) -> bool:
        return any(start < b and a < end for a, b in code)

    candidates: list[Span] = []
    for pattern, display in _PATTERNS:
        for m in pattern.finditer(text):
            if _in_code(m.start(), m.end()):
                continue
            candidates.append(
                Span(
                    start=m.start(),
                    end=m.end(),
                    body=m.group(1),
                    display=display,
                    raw=m.group(0),
                )
            )
    # Resolve overlaps: sort by start, then prefer the longer match at the
    # same start (display `$$` over inline `$`). Greedily accept spans that
    # do not overlap one already accepted.
    candidates.sort(key=lambda s: (s.start, -(s.end - s.start)))
    chosen: list[Span] = []
    claimed_until = -1
    for span in candidates:
        if span.start >= claimed_until:
            chosen.append(span)
            claimed_until = span.end
    return chosen


def protect(text: str) -> tuple[str, list[Span]]:
    """Replace math spans in `text` with inert placeholders.

    Returns `(masked, tokens)`. Feed `masked` through the Markdown renderer
    (the placeholders survive untouched), then call `restore` with the same
    `tokens` to swap the rendered math back in.
    """
    spans = find_spans(text)
    if not spans:
        return text, []
    out: list[str] = []
    last = 0
    for i, span in enumerate(spans):
        out.append(text[last:span.start])
        out.append(f"{_PH_OPEN}{i}{_PH_CLOSE}")
        last = span.end
    out.append(text[last:])
    return "".join(out), spans


def restore(rendered: str, tokens: list[Span], render) -> str:
    """Swap placeholders in `rendered` back for `render(span)`.

    `render` is a callback `(span: Span) -> str` returning the HTML (or
    text) to substitute for each math span; it is responsible for its own
    escaping.
    """
    if not tokens:
        return rendered

    def _sub(m: re.Match[str]) -> str:
        return render(tokens[int(m.group(1))])

    return _PH_RE.sub(_sub, rendered)


def has_placeholder(text: str) -> bool:
    """True if `text` still contains an unrestored math placeholder."""
    return bool(_PH_RE.search(text))
