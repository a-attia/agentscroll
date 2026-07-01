"""Render a Session to portable formats: markdown, json, html, text.

These are pure functions from a Session to a string, so they are trivial
to test and to reuse from both the CLI (export/copy) and the web app.
"""

from __future__ import annotations

import html as _html
import json
from dataclasses import asdict
from datetime import datetime

from . import minimd
from .models import Message, Part, Session

_ROLE_LABEL = {
    "user": "User",
    "assistant": "Assistant",
    "system": "System",
    "tool": "Tool",
}


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip() if dt else "?"


def _fmt_tokens(n: int | None) -> str:
    if not n:
        return "0"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1e3:.1f}k"
    return f"{n / 1e6:.1f}M"


def _usage_summary(session: Session) -> str:
    """A compact one-line usage string, or '' when the source reports none."""
    s = session
    if not any((s.tokens_input, s.tokens_output, s.tokens_cache_read,
                s.tokens_cache_write, s.tokens_reasoning, s.cost)):
        return ""
    bits = []
    if s.tokens_input or s.tokens_output:
        bits.append(f"{_fmt_tokens(s.tokens_input)} in / {_fmt_tokens(s.tokens_output)} out")
    if s.tokens_cache_read or s.tokens_cache_write:
        bits.append(f"cache {_fmt_tokens(s.tokens_cache_read)} read / "
                    f"{_fmt_tokens(s.tokens_cache_write)} write")
    if s.tokens_reasoning:
        bits.append(f"{_fmt_tokens(s.tokens_reasoning)} reasoning")
    if s.cost:
        bits.append(f"${s.cost:.2f}")
    return "; ".join(bits)


# -- markdown --------------------------------------------------------------


def to_markdown(session: Session, *, include_reasoning: bool = True,
                include_tools: bool = True, math: str = "raw") -> str:
    # Markdown export is verbatim text, so delimited LaTeX is already
    # preserved exactly; `math` is accepted for a uniform CLI/web surface
    # but does not transform the source (there is nothing to typeset in a
    # plain .md file).
    del math
    lines: list[str] = []
    lines.append(f"# {session.title}")
    lines.append("")
    lines.append(f"- **Source**: {session.source}")
    lines.append(f"- **Session**: `{session.id}`")
    if session.directory:
        lines.append(f"- **Directory**: `{session.directory}`")
    if session.model:
        lines.append(f"- **Model**: {session.model}")
    if session.agent:
        lines.append(f"- **Agent**: {session.agent}")
    lines.append(f"- **Created**: {_fmt_dt(session.created)}")
    lines.append(f"- **Updated**: {_fmt_dt(session.updated)}")
    lines.append(f"- **Messages**: {len(session.messages)}")
    usage = _usage_summary(session)
    if usage:
        lines.append(f"- **Usage**: {usage}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in session.messages:
        rendered = _markdown_message(msg, include_reasoning, include_tools)
        if rendered:
            lines.append(rendered)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _markdown_message(msg: Message, include_reasoning: bool, include_tools: bool) -> str:
    blocks: list[str] = []
    header = f"## {_ROLE_LABEL.get(msg.role, msg.role)}"
    when = _fmt_dt(msg.created)
    if when != "?":
        header += f"  \n*{when}*"
    blocks.append(header)
    for part in msg.parts:
        b = _markdown_part(part, include_reasoning, include_tools)
        if b:
            blocks.append(b)
    # Only emit the message if it has content beyond the header.
    return "\n\n".join(blocks) if len(blocks) > 1 else ""


def _markdown_part(part: Part, include_reasoning: bool, include_tools: bool) -> str:
    if part.type == "text":
        return part.text
    if part.type == "reasoning":
        if not include_reasoning or not part.text:
            return ""
        return "> **reasoning**\n>\n" + "\n".join(f"> {ln}" for ln in part.text.splitlines())
    if part.type == "tool":
        if not include_tools or not part.text:
            return ""
        label = part.tool_name or part.tool_status or "tool"
        status = f" ({part.tool_status})" if part.tool_status and part.tool_name else ""
        return f"**tool: {label}{status}**\n\n```\n{part.text}\n```"
    return ""


# -- json ------------------------------------------------------------------


def to_json(session: Session, *, indent: int = 2) -> str:
    def default(o: object) -> object:
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    payload = asdict(session)
    # Drop bulky raw blobs from the default JSON export for readability.
    payload.pop("raw", None)
    for m in payload.get("messages", []):
        m.pop("raw", None)
        for p in m.get("parts", []):
            p.pop("raw", None)
    return json.dumps(payload, indent=indent, default=default, ensure_ascii=False)


# -- text ------------------------------------------------------------------


def to_text(session: Session, *, include_reasoning: bool = False,
            include_tools: bool = True, math: str = "raw") -> str:
    # Plain-text export is verbatim; LaTeX is preserved as-is. `math` is a
    # no-op here (kept for a uniform export surface).
    del math
    lines = [session.title, "=" * len(session.title), ""]
    for msg in session.messages:
        role = _ROLE_LABEL.get(msg.role, msg.role).upper()
        chunk: list[str] = []
        for part in msg.parts:
            if part.type == "text" and part.text:
                chunk.append(part.text)
            elif part.type == "reasoning" and include_reasoning and part.text:
                chunk.append(f"[reasoning] {part.text}")
            elif part.type == "tool" and include_tools and part.text:
                label = part.tool_name or part.tool_status or "tool"
                chunk.append(f"[tool:{label}] {part.text}")
        if chunk:
            lines.append(f"--- {role} ---")
            lines.append("\n".join(chunk))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# -- html ------------------------------------------------------------------

_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font: 15px/1.6 -apple-system, system-ui, sans-serif; max-width: 820px;
        margin: 2rem auto; padding: 0 1rem; }}
.meta {{ color: #888; font-size: 13px; margin-bottom: 1.5rem; }}
.msg {{ border-radius: 10px; padding: .75rem 1rem; margin: .75rem 0; }}
.user {{ background: rgba(120,140,255,.12); }}
.assistant {{ background: rgba(140,140,140,.10); }}
.role {{ font-weight: 600; font-size: 12px; text-transform: uppercase;
         letter-spacing: .05em; opacity: .7; }}
.reasoning {{ opacity: .65; font-style: italic; border-left: 3px solid #aaa;
              padding-left: .75rem; margin: .5rem 0; }}
.tool {{ background: rgba(0,0,0,.06); border-radius: 6px; padding: .5rem .75rem;
         margin: .5rem 0; }}
pre {{ white-space: pre-wrap; word-break: break-word; margin: .25rem 0; }}
.tool-name {{ font-size: 12px; font-weight: 600; opacity: .7; }}
/* rendered markdown */
.md > *:first-child {{ margin-top: 0; }}
.md > *:last-child {{ margin-bottom: 0; }}
.md h1, .md h2, .md h3, .md h4 {{ line-height: 1.3; margin: 1em 0 .4em; }}
.md h1 {{ font-size: 1.5em; }} .md h2 {{ font-size: 1.3em; }}
.md h3 {{ font-size: 1.12em; }} .md h4 {{ font-size: 1em; }}
.md p {{ margin: .5em 0; }}
.md ul, .md ol {{ margin: .4em 0; padding-left: 1.5em; }}
.md li {{ margin: .15em 0; }}
.md a {{ color: #2a6fb0; }}
.md blockquote {{ margin: .5em 0; padding: .15em 0 .15em 1em;
                  border-left: 3px solid #ccc; color: #777; }}
.md hr {{ border: none; border-top: 1px solid #ccc; margin: 1em 0; }}
.md code {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .88em;
            background: rgba(127,127,127,.18); border-radius: 4px; padding: .1em .35em; }}
.md pre {{ background: rgba(127,127,127,.12); border: 1px solid rgba(127,127,127,.25);
           border-radius: 8px; padding: .7rem .9rem; overflow-x: auto; }}
.md pre code {{ background: none; padding: 0; }}
.math-src {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .9em;
             background: rgba(127,127,127,.18); border-radius: 4px; padding: .1em .35em; }}
.math-display {{ display: block; text-align: center; margin: .6em 0; overflow-x: auto; }}
{hl_css}
@media print {{
  body {{ max-width: none; }}
  .msg {{ break-inside: avoid; }}
  .tool, .md pre {{ break-inside: avoid; }}
}}
</style>
{math_head}
</head><body>
<h1>{title}</h1>
<div class="meta">{meta}</div>
{body}
{math_body}
</body></html>
"""


def to_html(session: Session, *, include_reasoning: bool = True,
            include_tools: bool = True, math: str = "raw") -> str:
    meta_bits = [
        f"source: {session.source}",
        f"id: {session.id}",
    ]
    if session.directory:
        meta_bits.append(f"dir: {session.directory}")
    if session.model:
        meta_bits.append(f"model: {session.model}")
    meta_bits.append(f"created: {_fmt_dt(session.created)}")
    meta_bits.append(f"messages: {len(session.messages)}")
    usage = _usage_summary(session)
    if usage:
        meta_bits.append(f"usage: {usage}")
    meta = " &middot; ".join(_html.escape(b) for b in meta_bits)

    body_parts: list[str] = []
    for msg in session.messages:
        inner = _html_message(msg, include_reasoning, include_tools, math)
        if inner:
            body_parts.append(inner)

    # In `rendered` mode embed KaTeX so the static file typesets offline; in
    # the other modes the math is inert source (no asset needed).
    math_head = math_body = ""
    if math == "rendered":
        from . import katexbundle

        math_head = katexbundle.head_assets()
        math_body = katexbundle.autorender_script()

    return _HTML_TEMPLATE.format(
        title=_html.escape(session.title),
        meta=meta,
        body="\n".join(body_parts),
        hl_css=minimd_highlight_css(),
        math_head=math_head,
        math_body=math_body,
    )


def minimd_highlight_css() -> str:
    from . import highlight

    return highlight.HL_CSS


def _html_message(msg: Message, include_reasoning: bool, include_tools: bool,
                  math: str = "raw") -> str:
    inner: list[str] = []
    for part in msg.parts:
        if part.type == "text" and part.text:
            # Render markdown (stdlib-only) so the static export reads nicely.
            inner.append(f'<div class="md">{minimd.render(part.text, math=math)}</div>')
        elif part.type == "reasoning" and include_reasoning and part.text:
            inner.append(f'<div class="reasoning"><pre>{_html.escape(part.text)}</pre></div>')
        elif part.type == "tool" and include_tools and part.text:
            name = _html.escape(part.tool_name or part.tool_status or "tool")
            inner.append(
                f'<div class="tool"><div class="tool-name">{name}</div>'
                f"<pre>{_html.escape(part.text)}</pre></div>"
            )
    if not inner:
        return ""
    role = _ROLE_LABEL.get(msg.role, msg.role)
    cls = msg.role if msg.role in ("user", "assistant") else "assistant"
    return (
        f'<div class="msg {cls}"><div class="role">{_html.escape(role)}</div>'
        + "\n".join(inner)
        + "</div>"
    )


FORMATS = {
    "markdown": to_markdown,
    "md": to_markdown,
    "json": to_json,
    "html": to_html,
    "text": to_text,
    "txt": to_text,
}

# Math render modes for delimited-LaTeX spans (see minimd.render / katexbundle):
#   raw      -- verbatim source, shielded from the Markdown pass
#   latex    -- verbatim source, wrapped so it is never typeset (paste-ready)
#   rendered -- typeset with KaTeX (HTML export embeds KaTeX to do so offline)
MATH_MODES = ("raw", "latex", "rendered")


def render(session: Session, fmt: str, **kwargs: object) -> str:
    func = FORMATS.get(fmt)
    if func is None:
        raise ValueError(f"unknown format: {fmt!r}; choose from {sorted(set(FORMATS))}")
    return func(session, **kwargs)  # type: ignore[arg-type]
