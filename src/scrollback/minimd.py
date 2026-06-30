"""A tiny, safe, stdlib-only Markdown-to-HTML renderer.

Scope is deliberately small -- the common constructs that appear in AI
chat transcripts: fenced code blocks, ATX headings, unordered/ordered
lists, blockquotes, horizontal rules, paragraphs, and inline spans
(code, bold, italic, links). Everything is HTML-escaped first, so the
output is safe to drop into an export without an external dependency and
without risking HTML/script injection from transcript content.

This is NOT a CommonMark implementation; it trades completeness for zero
dependencies and predictable, safe output. The richer browser view uses
the vendored `marked` + `highlight.js`; this module exists so the static
HTML *export* renders nicely on its own (e.g. for printing/sharing).
"""

from __future__ import annotations

import html as _html
import re

from . import highlight as _highlight
from . import mathspan as _mathspan

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_ITEM = re.compile(r"^[ \t]*[-*+]\s+(.*)$")
_OL_ITEM = re.compile(r"^[ \t]*\d+[.)]\s+(.*)$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_BLOCKQUOTE = re.compile(r"^>\s?(.*)$")


def render(text: str, *, math: str = "raw") -> str:
    """Render Markdown `text` to a safe HTML fragment.

    `math` controls how delimited-LaTeX spans (`$...$`, `$$...$$`,
    `\\(...\\)`, `\\[...\\]`) are handled. In every mode the span is first
    shielded from the Markdown pass so `\\`, `_`, `*`, `^` survive intact:

    - ``"raw"``     -- restore the original delimited source verbatim
      (default; matches the historical behaviour but no longer mangled).
    - ``"latex"``   -- show the LaTeX source verbatim, wrapped so a renderer
      will not typeset it; best for copying into a paper.
    - ``"rendered"`` -- emit a placeholder element the client typesets with
      KaTeX (the static export embeds KaTeX so this works offline).
    """
    masked, tokens = _mathspan.protect(text)
    html = _render_blocks(masked)
    return _mathspan.restore(html, tokens, lambda span: _math_html(span, math))


def _math_html(span: _mathspan.Span, mode: str) -> str:
    """Render one math span to HTML for the static export, per `mode`."""
    if mode == "rendered":
        # Emit the LaTeX in a span the client-side KaTeX pass typesets; the
        # body is escaped so it is inert until KaTeX reads textContent.
        cls = "math-tex math-display" if span.display else "math-tex"
        disp = "true" if span.display else "false"
        return f'<span class="{cls}" data-display="{disp}">{_html.escape(span.body)}</span>'
    if mode == "latex":
        # Verbatim source, never typeset, never mangled.
        return f'<code class="math-src">{_html.escape(span.raw)}</code>'
    # raw: restore the original delimited source verbatim, as inert text.
    return _html.escape(span.raw)


def _render_blocks(text: str) -> str:
    """Render Markdown block structure (math already masked by `render`)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Fenced code block.
        m = _FENCE.match(line)
        if m:
            fence = m.group(1)[0]
            lang = m.group(2).strip()
            j = i + 1
            buf: list[str] = []
            while j < n and not _is_closing_fence(lines[j], fence):
                buf.append(lines[j])
                j += 1
            raw = "\n".join(buf)
            if lang:
                # Highlighter escapes internally and emits only safe spans.
                code = _highlight.highlight(raw, lang)
                cls = f' class="language-{_html.escape(lang)}"'
            else:
                code = _html.escape(raw)
                cls = ""
            out.append(f"<pre><code{cls}>{code}</code></pre>")
            i = j + 1
            continue

        # Blank line.
        if not line.strip():
            i += 1
            continue

        # Horizontal rule.
        if _HR.match(line):
            out.append("<hr>")
            i += 1
            continue

        # Heading.
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # Blockquote (consecutive '>' lines).
        if _BLOCKQUOTE.match(line):
            buf = []
            while i < n and _BLOCKQUOTE.match(lines[i]):
                buf.append(_BLOCKQUOTE.match(lines[i]).group(1))
                i += 1
            inner = _render_blocks("\n".join(buf))
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        # Lists (unordered / ordered).
        if _UL_ITEM.match(line) or _OL_ITEM.match(line):
            ordered = bool(_OL_ITEM.match(line))
            pat = _OL_ITEM if ordered else _UL_ITEM
            items: list[str] = []
            while i < n and pat.match(lines[i]):
                items.append(_inline(pat.match(lines[i]).group(1).strip()))
                i += 1
            tag = "ol" if ordered else "ul"
            li = "".join(f"<li>{it}</li>" for it in items)
            out.append(f"<{tag}>{li}</{tag}>")
            continue

        # Paragraph: gather consecutive non-blank, non-special lines.
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            buf.append(lines[i])
            i += 1
        para = "<br>".join(_inline(b.strip()) for b in buf)
        out.append(f"<p>{para}</p>")

    return "\n".join(out)


def _is_closing_fence(line: str, fence_char: str) -> bool:
    m = _FENCE.match(line)
    return bool(m and m.group(1)[0] == fence_char)


def _starts_block(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _UL_ITEM.match(line)
        or _OL_ITEM.match(line)
        or _HR.match(line)
        or _FENCE.match(line)
        or _BLOCKQUOTE.match(line)
    )


# -- inline spans ----------------------------------------------------------

# Process inline code first (and protect its contents), then links, then
# emphasis. All raw text is escaped before markup substitution.
_CODE_SPAN = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])|(?<![_\w])_([^_\n]+)_(?![_\w])")


def _inline(text: str) -> str:
    # Extract code spans first so their contents are not touched by other
    # rules; replace with placeholders, then restore at the end.
    placeholders: list[str] = []

    def _stash_code(m: re.Match) -> str:
        placeholders.append(f"<code>{_html.escape(m.group(1))}</code>")
        return f"\x00{len(placeholders) - 1}\x00"

    tmp = _CODE_SPAN.sub(_stash_code, text)
    tmp = _html.escape(tmp)

    # Links: [label](url) -> escape both parts.
    def _link(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2)
        return f'<a href="{_html.escape(url, quote=True)}">{label}</a>'

    # Note: tmp is already escaped, so the bracket/paren chars survive as-is.
    tmp = _LINK.sub(_link, tmp)
    tmp = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", tmp)
    tmp = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", tmp)

    # Restore code spans.
    def _restore(m: re.Match) -> str:
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, tmp)
