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
from datetime import datetime, timezone

from . import __version__, clipboard, export, serverconfig
from .models import Session
from .sources import registry
from .store import Store


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr)


def _nonneg_int(s: str) -> int:
    """argparse type: a non-negative integer (rejects negatives)."""
    try:
        v = int(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {s!r}") from exc
    if v < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {v}")
    return v


def _positive_int(s: str) -> int:
    """argparse type: a positive integer (>= 1)."""
    v = _nonneg_int(s)
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {v}")
    return v


def _parse_date(s: str | None) -> datetime | None:
    """Parse a CLI date/datetime into an aware UTC datetime.

    Accepts YYYY-MM-DD or full ISO-8601. Naive values are treated as UTC.
    Raises argparse.ArgumentTypeError on bad input so the CLI reports it.
    """
    if not s:
        return None
    raw = s.strip()
    try:
        if len(raw) == 10:  # YYYY-MM-DD
            dt = datetime.strptime(raw, "%Y-%m-%d")
        else:
            iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {s!r}; use YYYY-MM-DD or ISO-8601"
        ) from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_tokens(n: int | None) -> str:
    """Compact token count: 12345 -> '12.3k', 2100000 -> '2.1M'."""
    if n is None:
        return ""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def _fmt_cost(c: float | None) -> str:
    return f"${c:.2f}" if c else ""


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
    offset = args.offset
    if args.page and args.page > 1:
        offset = (args.page - 1) * args.limit
    sessions = store.list_sessions(
        directory=args.dir,
        query=args.query,
        since=args.since,
        until=args.until,
        limit=args.limit,
        offset=offset,
        fold_subagents=not args.no_fold,
    )
    if not sessions:
        _eprint("no sessions found")
        return 1
    if args.json:
        import json

        def row(s: Session) -> dict[str, object]:
            return {
                "id": s.id,
                "source": s.source,
                "title": s.title,
                "directory": s.directory,
                "updated": s.updated.isoformat() if s.updated else None,
                "model": s.model,
                "agent": s.agent,
                "messages": s.message_count,
                "cost": s.cost,
                "tokens_input": s.tokens_input,
                "tokens_output": s.tokens_output,
                "parent_id": s.parent_id,
                "children": [row(c) for c in s.children],
            }

        print(json.dumps([row(s) for s in sessions], indent=2, ensure_ascii=False))
        return 0

    from . import termrender

    if termrender.available(force=_color_force(args)):
        termrender.render_list(sessions, show_usage=args.usage)
    else:
        if args.usage:
            _eprint(
                f"{'source':10} {'id':13} {'updated':16} {'msgs':>9} "
                f"{'cost':>7} {'tok in/out':>14}  title"
            )
        _print_list(sessions, show_usage=args.usage)
    if offset:
        _eprint(f"(offset {offset})")
    return 0


def _color_force(args: argparse.Namespace) -> bool | None:
    """Translate --plain into a force flag for termrender.available()."""
    if getattr(args, "plain", False):
        return False
    return None


def _print_list(sessions: list[Session], *, show_usage: bool, indent: str = "") -> None:
    for s in sessions:
        msgs = f"{s.message_count:>4}" if s.message_count is not None else "   ?"
        usage = ""
        if show_usage:
            toks = f"{_fmt_tokens(s.tokens_input)}/{_fmt_tokens(s.tokens_output)}"
            cost = _fmt_cost(s.cost)
            usage = f" {cost:>7} {toks:>14}"
        marker = "\u2514 " if indent else ""
        print(
            f"{indent}{marker}{s.source:10} {s.short_id:13} {_fmt_dt(s.updated):16} "
            f"{msgs} msgs{usage}  {s.title}"
        )
        if s.children:
            _print_list(list(s.children), show_usage=show_usage, indent=indent + "  ")


def _resolve(store: Store, args: argparse.Namespace) -> Session | None:
    return store.load_session(args.selector, source=getattr(args, "source", None))


def cmd_show(args: argparse.Namespace) -> int:
    store = _make_store(args)
    sess = _resolve(store, args)
    if sess is None:
        _eprint(f"session not found: {args.selector}")
        return 1
    from . import termrender

    if termrender.available(force=_color_force(args)):
        termrender.render_transcript(
            sess,
            include_reasoning=args.reasoning,
            include_tools=not args.no_tools,
            markdown=not args.no_markdown,
        )
        return 0
    text = export.to_text(
        sess,
        include_reasoning=args.reasoning,
        include_tools=not args.no_tools,
    )
    print(text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = _make_store(args)
    hits = list(
        store.search(
            args.query,
            directory=args.dir,
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
    )
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
    from . import termrender

    if termrender.available(force=_color_force(args)):
        termrender.render_search(hits, args.query)
    else:
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
    from . import serverconfig
    from .web.app import create_app

    # Resolve the actual port to bind: honour the requested one, but fall back
    # to the next free port if it's taken (unless --strict-port). This keeps a
    # single source of truth and avoids "started on a different port than the
    # URL we opened" bugs -- the resolved port is used everywhere below.
    try:
        port = serverconfig.resolve_port(args.host, args.port, strict=args.strict_port)
    except OSError as exc:
        _eprint(str(exc))
        return 1
    if port != args.port:
        _eprint(f"port {args.port} busy; using {port} instead")
    args.port = port  # so downstream (app-window mode) sees the real port

    url = f"http://{args.host}:{port}"

    # If binding to a non-loopback address, warn loudly: the API is
    # unauthenticated and would expose all local AI history to the network.
    # Add the chosen host to the Host-guard allowlist so it can be reached.
    loopback = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}
    allowed_hosts = None
    if args.host not in loopback:
        _eprint(f"WARNING: binding to non-loopback host {args.host!r}; the read-only "
                "API will be reachable from the network with no authentication.")
        allowed_hosts = [args.host]
    elif args.host == "0.0.0.0":
        _eprint("WARNING: binding to 0.0.0.0 exposes the API on all interfaces.")
        allowed_hosts = []  # can't know the external hostname; disable host guard

    # Desktop "app window" mode: a true native window via pywebview. Closing
    # the window quits the process -> server stops -> port is freed, and there
    # is no terminal. If pywebview isn't available, fall back to a browser
    # window (with heartbeat auto-shutdown) instead of failing.
    if getattr(args, "app", False):
        if _pywebview_available():
            return _run_app_window(create_app(allowed_hosts=allowed_hosts), args, url)
        _eprint("native window unavailable (pywebview not installed/usable); "
                "opening a browser window with auto-shutdown instead")
        args.window = True
        args.auto_shutdown = True  # browser fallback: stop server when window closes

    # Optional heartbeat auto-shutdown: stop the server shortly after the
    # browser window/tab is closed (so the port is freed without Ctrl-C).
    server_holder: dict[str, object] = {}

    def _on_idle() -> None:
        srv = server_holder.get("server")
        if srv is not None:
            srv.should_exit = True

    if getattr(args, "auto_shutdown", False):
        app = create_app(on_idle=_on_idle, idle_timeout=10.0, allowed_hosts=allowed_hosts)
    else:
        app = create_app(allowed_hosts=allowed_hosts)

    _eprint(f"agentscroll web -> {url}  (read-only; Ctrl-C to stop)")
    if not args.no_browser:
        import threading

        from . import webopen

        # Open after a short delay so the server is accepting connections.
        # `--window` asks for a standalone window; default opens a tab.
        opener = webopen.open_window if args.window else _open_tab
        threading.Timer(0.8, lambda: opener(url)).start()

    server = uvicorn.Server(
        uvicorn.Config(app, host=args.host, port=port, log_level="warning")
    )
    server_holder["server"] = server
    server.run()
    return 0


def _pywebview_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("webview") is not None


def _open_tab(url: str) -> str:
    import webbrowser

    return "tab" if webbrowser.open(url) else "failed"


def cmd_install_launcher(args: argparse.Namespace) -> int:
    from . import launcher_install

    dest = None
    if args.dest:
        import pathlib

        dest = pathlib.Path(args.dest).expanduser()
    try:
        created = launcher_install.install(dest, app_bundle=args.app_bundle)
    except OSError as exc:
        _eprint(f"could not install launcher: {exc}")
        return 1
    if not created:
        _eprint("nothing was installed")
        return 1
    _eprint("installed launcher(s):")
    for p in created:
        _eprint(f"  {p}")
    if sys.platform == "darwin" and not args.app_bundle:
        _eprint("tip: double-click it (first time: right-click -> Open).")
        _eprint("     for an app icon in ~/Applications, re-run with --app-bundle")
    return 0


def _run_app_window(app: object, args: argparse.Namespace, url: str) -> int:
    try:
        import webview  # pywebview
    except ModuleNotFoundError:
        _eprint(
            "the desktop app window needs pywebview. install with:\n"
            '    pip install "agentscroll[app]"\n'
            "or just run without --app to use your browser."
        )
        return 1
    import threading

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    _eprint(f"agentscroll app -> {url}  (read-only; close the window to quit)")
    webview.create_window("agentscroll", url, width=1280, height=860)
    webview.start()  # blocks until the window is closed
    # Window closed: stop the server and wait for the port to be released so
    # an immediate relaunch can reuse it.
    server.should_exit = True
    t.join(timeout=5)
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
    sp.add_argument("--since", type=_parse_date, metavar="DATE",
                    help="only sessions updated on/after DATE (YYYY-MM-DD or ISO)")
    sp.add_argument("--until", type=_parse_date, metavar="DATE",
                    help="only sessions updated on/before DATE")
    sp.add_argument("-n", "--limit", type=_positive_int, default=30, help="max rows (default 30)")
    sp.add_argument("--offset", type=_nonneg_int, default=0, help="skip N rows (pagination)")
    sp.add_argument("--page", type=_positive_int, help="page number (uses --limit as page size)")
    sp.add_argument("--usage", action="store_true", help="show cost + token columns")
    sp.add_argument("--no-fold", action="store_true",
                    help="do not nest subagent sessions under their parent")
    sp.add_argument("--plain", action="store_true", help="disable colour output")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="print a session transcript")
    add_source_flag(sp)
    sp.add_argument("selector", help="session id / prefix / source:id / latest")
    sp.add_argument("--reasoning", action="store_true", help="include reasoning blocks")
    sp.add_argument("--no-tools", action="store_true", help="hide tool calls/outputs")
    sp.add_argument("--no-markdown", action="store_true",
                    help="render text as plain (no markdown formatting)")
    sp.add_argument("--plain", action="store_true", help="disable colour output")
    sp.set_defaults(func=cmd_show)

    # search
    sp = sub.add_parser("search", help="search across sessions")
    add_source_flag(sp)
    sp.add_argument("query", help="text to search for (case-insensitive)")
    sp.add_argument("--dir", help="filter by directory substring")
    sp.add_argument("--since", type=_parse_date, metavar="DATE",
                    help="only sessions updated on/after DATE")
    sp.add_argument("--until", type=_parse_date, metavar="DATE",
                    help="only sessions updated on/before DATE")
    sp.add_argument("-n", "--limit", type=_positive_int, default=50, help="max hits (default 50)")
    sp.add_argument("--plain", action="store_true", help="disable colour output")
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
    sp.add_argument("--host", default=serverconfig.default_host(),
                    help="bind host (default localhost; or $AGENTSCROLL_HOST)")
    sp.add_argument("-p", "--port", type=int, default=serverconfig.default_port(),
                    help=f"port (default {serverconfig.DEFAULT_PORT}; or $AGENTSCROLL_PORT)")
    sp.add_argument("--strict-port", action="store_true",
                    help="fail if the port is busy instead of picking the next free one")
    sp.add_argument("--no-browser", action="store_true", help="do not open a browser")
    sp.add_argument("--window", action="store_true",
                    help="open in a standalone browser window instead of a tab")
    sp.add_argument("--app", action="store_true",
                    help="open in a native desktop window (auto-closes; needs pywebview)")
    sp.add_argument("--auto-shutdown", action="store_true",
                    help="stop the server shortly after the browser window is closed")
    sp.set_defaults(func=cmd_web)

    # install-launcher
    sp = sub.add_parser(
        "install-launcher",
        help="install a double-clickable launcher for the web app",
    )
    sp.add_argument("--dest", help="where to place the launcher (default: Desktop)")
    sp.add_argument("--app-bundle", action="store_true",
                    help="macOS: also build an agentscroll.app in ~/Applications")
    sp.set_defaults(func=cmd_install_launcher)

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


def main_web(argv: list[str] | None = None) -> int:
    """Console entry point: `agentscroll-web [options]` == `agentscroll web`."""
    return main(["web", *(argv if argv is not None else sys.argv[1:])])


def main_app(argv: list[str] | None = None) -> int:
    """Console entry point: `agentscroll-app` == `agentscroll web --app`."""
    return main(["web", "--app", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
