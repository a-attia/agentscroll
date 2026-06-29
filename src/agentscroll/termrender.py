"""Optional rich-powered terminal rendering.

If the `rich` package is installed and output is a TTY, these functions
render colourful tables/transcripts; otherwise callers fall back to the
plain renderers in cli.py. Importing this module never fails when rich is
absent -- `available()` reports the capability.
"""

from __future__ import annotations

import sys
from datetime import datetime

from .models import Session
from .store import SearchHit

try:  # pragma: no cover - exercised indirectly
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    _RICH = True
except ModuleNotFoundError:  # pragma: no cover
    _RICH = False


# Source -> accent colour, mirroring the web UI's source rails.
_SRC_STYLE = {"opencode": "yellow", "claudecode": "dark_orange3"}


def available(force: bool | None = None) -> bool:
    """True if rich rendering should be used.

    `force=True`/`False` overrides the auto-detection (used by --plain and
    a future --color flag). Default: rich installed AND stdout is a TTY.
    """
    if force is not None:
        return force and _RICH
    return _RICH and sys.stdout.isatty()


def _console() -> "Console":
    return Console()


def _src_style(source: str) -> str:
    return _SRC_STYLE.get(source, "cyan")


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return ""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def render_list(sessions: list[Session], *, show_usage: bool) -> None:
    console = _console()
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("source", no_wrap=True)
    table.add_column("id", no_wrap=True, style="dim")
    table.add_column("updated", no_wrap=True, style="dim")
    table.add_column("msgs", justify="right", no_wrap=True)
    if show_usage:
        table.add_column("cost", justify="right", no_wrap=True)
        table.add_column("tok in/out", justify="right", no_wrap=True)
    table.add_column("title")

    def add(s: Session, depth: int = 0) -> None:
        prefix = ("  " * depth + "└ ") if depth else ""
        row = [
            Text(s.source, style=_src_style(s.source)),
            s.short_id,
            _fmt_dt(s.updated),
            str(s.message_count if s.message_count is not None else "?"),
        ]
        if show_usage:
            row.append(f"${s.cost:.2f}" if s.cost else "")
            row.append(f"{_fmt_tokens(s.tokens_input)}/{_fmt_tokens(s.tokens_output)}")
        row.append(Text(prefix + (s.title or "(untitled)"),
                        style="dim" if depth else ""))
        table.add_row(*row)
        for child in s.children:
            add(child, depth + 1)

    for s in sessions:
        add(s)
    console.print(table)


def render_search(hits: list[SearchHit], query: str) -> None:
    console = _console()
    ql = query.lower()
    for h in hits:
        head = Text()
        head.append(f"{h.session.source}", style=_src_style(h.session.source))
        head.append(f":{h.session.short_id} ", style="dim")
        head.append(f"[{h.message.role}]", style="bold")
        if h.part.tool_name:
            head.append(f" {h.part.tool_name}", style="cyan")
        console.print(head)
        snippet = Text(h.snippet, style="dim")
        snippet.highlight_words([query], "black on yellow") if False else None
        # Manual highlight of all case-insensitive matches.
        low = h.snippet.lower()
        start = 0
        styled = Text()
        idx = low.find(ql, start)
        if ql and idx != -1:
            while idx != -1:
                styled.append(h.snippet[start:idx], style="dim")
                styled.append(h.snippet[idx:idx + len(query)], style="bold black on yellow")
                start = idx + len(query)
                idx = low.find(ql, start)
            styled.append(h.snippet[start:], style="dim")
            console.print("  ", styled)
        else:
            console.print("  ", snippet)


def render_transcript(sess: Session, *, include_reasoning: bool, include_tools: bool) -> None:
    console = _console()
    title = Text(sess.title or "(untitled)", style="bold")
    console.print(title)
    meta = Text(
        f"{sess.source}  {sess.short_id}  {_fmt_dt(sess.created)}  "
        f"{len(sess.messages)} messages",
        style="dim",
    )
    console.print(meta)
    console.print()

    for msg in sess.messages:
        role_style = "cyan" if msg.role == "user" else "green"
        printed_role = False
        for part in msg.parts:
            text = ""
            style = None
            if part.type == "text" and part.text:
                text = part.text
            elif part.type == "reasoning" and include_reasoning and part.text:
                text = part.text
                style = "dim italic"
            elif part.type == "tool" and include_tools and part.text:
                label = part.tool_name or part.tool_status or "tool"
                if not printed_role:
                    console.print(Text(msg.role.upper(), style=f"bold {role_style}"))
                    printed_role = True
                console.print(Text(f"  $ {label}", style="yellow"))
                console.print(Text("  " + part.text.replace("\n", "\n  "), style="dim"))
                continue
            if not text:
                continue
            if not printed_role:
                console.print(Text(msg.role.upper(), style=f"bold {role_style}"))
                printed_role = True
            console.print(Text(text, style=style) if style else text)
        if printed_role:
            console.print()
