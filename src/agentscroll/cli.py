"""agentscroll command-line interface.

Subcommands:
  sources                 list detected agents and where they read from
  list                    list sessions (newest first), with filters
  show <selector>         print a session transcript to the terminal
  search <query>          search across sessions
  export <selector>       render a session to markdown/json/html/text
  copy <selector>         copy a rendered session to the clipboard

Selectors accept a full id, a unique prefix, `source:id`, or `latest`.
All output is plain and pipe-friendly. Reads are strictly read-only.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import __version__, clipboard, export
from .models import Session
from .sources import registry
from .store import Store


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr)


# -- subcommand implementations -------------------------------------------


def cmd_sources(args: argparse.Namespace) -> int:
    any_found = False
    for src in registry.all_sources():
        avail = src.is_available()
        any_found = any_found or avail
        loc = src.location()
        status = "available" if avail else "not found"
        print(f"{src.name:12} {status:12} {loc if loc else ''}")
    return 0 if any_found else 1


def _make_store(args: argparse.Namespace) -> Store:
    store = Store()
    if getattr(args, "source", None):
        store = store.with_sources([args.source])
    return store


def cmd_list(args: argparse.Namespace) -> int:
    store = _make_store(args)
    sessions = store.list_sessions(
        directory=args.dir,
        query=args.query,
        limit=args.limit,
    )
    if not sessions:
        _eprint("no sessions found")
        return 1
    if args.json:
        import json

        rows = [
            {
                "id": s.id,
                "source": s.source,
                "title": s.title,
                "directory": s.directory,
                "updated": s.updated.isoformat() if s.updated else None,
                "model": s.model,
                "agent": s.agent,
                "messages": s.message_count,
            }
            for s in sessions
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    for s in sessions:
        msgs = f"{s.message_count:>4}" if s.message_count is not None else "   ?"
        print(
            f"{s.source:10} {s.short_id:13} {_fmt_dt(s.updated):16} "
            f"{msgs} msgs  {s.title}"
        )
    return 0


def _resolve(store: Store, args: argparse.Namespace) -> Session | None:
    return store.load_session(args.selector, source=getattr(args, "source", None))


def cmd_show(args: argparse.Namespace) -> int:
    store = _make_store(args)
    sess = _resolve(store, args)
    if sess is None:
        _eprint(f"session not found: {args.selector}")
        return 1
    text = export.to_text(
        sess,
        include_reasoning=args.reasoning,
        include_tools=not args.no_tools,
    )
    print(text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = _make_store(args)
    hits = list(store.search(args.query, directory=args.dir, limit=args.limit))
    if not hits:
        _eprint("no matches")
        return 1
    if args.json:
        import json

        rows = [
            {
                "source": h.session.source,
                "session_id": h.session.id,
                "title": h.session.title,
                "message_id": h.message.id,
                "role": h.message.role,
                "part_type": h.part.type,
                "snippet": h.snippet,
            }
            for h in hits
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    for h in hits:
        print(f"{h.session.source}:{h.session.short_id} [{h.message.role}] {h.snippet}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = _make_store(args)
    sess = _resolve(store, args)
    if sess is None:
        _eprint(f"session not found: {args.selector}")
        return 1
    kwargs = _render_kwargs(args.format, args)
    rendered = export.render(sess, args.format, **kwargs)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        _eprint(f"wrote {args.output}")
    else:
        print(rendered)
    return 0


def cmd_copy(args: argparse.Namespace) -> int:
    store = _make_store(args)
    sess = _resolve(store, args)
    if sess is None:
        _eprint(f"session not found: {args.selector}")
        return 1
    kwargs = _render_kwargs(args.format, args)
    rendered = export.render(sess, args.format, **kwargs)
    if clipboard.copy(rendered):
        _eprint(f"copied {len(rendered)} chars ({args.format}) to clipboard")
        return 0
    _eprint("clipboard unavailable; printing instead")
    print(rendered)
    return 1


def _render_kwargs(fmt: str, args: argparse.Namespace) -> dict[str, object]:
    if fmt == "json":
        return {}
    return {
        "include_reasoning": args.reasoning,
        "include_tools": not args.no_tools,
    }


def cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ModuleNotFoundError:
        _eprint(
            "the web app needs extra dependencies. install with:\n"
            '    pip install "agentscroll[web]"\n'
            "or:\n"
            "    pip install fastapi uvicorn"
        )
        return 1
    from .web.app import create_app

    app = create_app()
    url = f"http://{args.host}:{args.port}"
    _eprint(f"agentscroll web -> {url}  (read-only; Ctrl-C to stop)")
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


# -- argument parser -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentscroll",
        description="Navigate, search, copy, and export AI coding-agent sessions.",
    )
    p.add_argument("--version", action="version", version=f"agentscroll {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # sources
    sp = sub.add_parser("sources", help="list detected agents")
    sp.set_defaults(func=cmd_sources)

    # common filters
    def add_source_flag(sp_: argparse.ArgumentParser) -> None:
        sp_.add_argument("--source", help="restrict to one source (e.g. opencode)")

    # list
    sp = sub.add_parser("list", help="list sessions (newest first)")
    add_source_flag(sp)
    sp.add_argument("--dir", help="filter by directory substring")
    sp.add_argument("-q", "--query", help="filter by title substring")
    sp.add_argument("-n", "--limit", type=int, default=30, help="max rows (default 30)")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="print a session transcript")
    add_source_flag(sp)
    sp.add_argument("selector", help="session id / prefix / source:id / latest")
    sp.add_argument("--reasoning", action="store_true", help="include reasoning blocks")
    sp.add_argument("--no-tools", action="store_true", help="hide tool calls/outputs")
    sp.set_defaults(func=cmd_show)

    # search
    sp = sub.add_parser("search", help="search across sessions")
    add_source_flag(sp)
    sp.add_argument("query", help="text to search for (case-insensitive)")
    sp.add_argument("--dir", help="filter by directory substring")
    sp.add_argument("-n", "--limit", type=int, default=50, help="max hits (default 50)")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=cmd_search)

    # export
    sp = sub.add_parser("export", help="render a session to a file/stdout")
    add_source_flag(sp)
    sp.add_argument("selector", help="session id / prefix / source:id / latest")
    sp.add_argument(
        "-f", "--format", default="markdown",
        choices=sorted(set(export.FORMATS)), help="output format",
    )
    sp.add_argument("-o", "--output", help="write to file instead of stdout")
    sp.add_argument("--reasoning", action="store_true", help="include reasoning blocks")
    sp.add_argument("--no-tools", action="store_true", help="hide tool calls/outputs")
    sp.set_defaults(func=cmd_export)

    # copy
    sp = sub.add_parser("copy", help="copy a rendered session to the clipboard")
    add_source_flag(sp)
    sp.add_argument("selector", help="session id / prefix / source:id / latest")
    sp.add_argument(
        "-f", "--format", default="markdown",
        choices=sorted(set(export.FORMATS)), help="render format",
    )
    sp.add_argument("--reasoning", action="store_true", help="include reasoning blocks")
    sp.add_argument("--no-tools", action="store_true", help="hide tool calls/outputs")
    sp.set_defaults(func=cmd_copy)

    # web
    sp = sub.add_parser("web", help="launch the local web app (read-only)")
    sp.add_argument("--host", default="127.0.0.1", help="bind host (default localhost)")
    sp.add_argument("-p", "--port", type=int, default=8765, help="port (default 8765)")
    sp.add_argument("--no-browser", action="store_true", help="do not open a browser")
    sp.set_defaults(func=cmd_web)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
