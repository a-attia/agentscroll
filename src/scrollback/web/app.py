"""FastAPI application exposing a read-only JSON API over the Store.

Design notes
------------
* Strictly read-only: there are no mutating endpoints. The Store and its
  adapters never write to the agents' data.
* Intended to bind to 127.0.0.1 only (enforced by the `web` CLI command).
* The frontend is static (HTML/CSS/JS) served from `web/static/`; the
  browser talks to the JSON endpoints below.

Endpoints
---------
GET /api/sources                     -> available source adapters
GET /api/sessions?source&dir&q&limit -> session summaries (newest first)
GET /api/sessions/{source}/{id}      -> full session with messages/parts
GET /api/search?q&dir&limit          -> search hits across sessions
GET /api/export/{source}/{id}?format&reasoning&tools -> rendered document
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Query, Response
    from fastapi.staticfiles import StaticFiles
except ModuleNotFoundError as exc:  # pragma: no cover - guidance path
    raise SystemExit(
        "The web app needs FastAPI/uvicorn. Install with:\n"
        '    pip install "scrollback[web]"\n'
        "or:\n"
        "    pip install fastapi uvicorn"
    ) from exc

from pathlib import Path

from datetime import datetime, timezone

from .. import __version__, export
from ..serialize import message_dict, search_hit, session_detail, session_summary
from ..store import Store


def _parse_dt(s: str | None) -> datetime | None:
    """Parse an ISO date/datetime from a query param; None if blank/invalid."""
    if not s:
        return None
    raw = s.strip()
    try:
        if len(raw) == 10:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

_STATIC_DIR = Path(__file__).parent / "static"

# Content types for the export endpoint.
_MEDIA = {
    "markdown": "text/markdown; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "text": "text/plain; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}
_EXT = {"markdown": "md", "md": "md", "json": "json", "html": "html",
        "text": "txt", "txt": "txt"}


def create_app(
    store: Store | None = None,
    *,
    on_idle=None,
    idle_timeout: float = 0.0,
    allowed_hosts: list[str] | None = None,
):
    """Build the FastAPI app. A custom Store can be injected for tests.

    If `idle_timeout` > 0 and `on_idle` is provided, the app runs a watchdog:
    the frontend pings `/api/heartbeat` periodically, and if no ping arrives
    for `idle_timeout` seconds (i.e. the window was closed), `on_idle()` is
    called -- used to auto-stop the server and free the port.

    `allowed_hosts` guards against DNS-rebinding: requests whose Host header's
    hostname is not in the allowlist are rejected. Defaults to loopback names
    (localhost / 127.0.0.1 / ::1). Pass an explicit list when binding to a
    non-loopback address. `None` => loopback-only; an empty list disables the
    check (not recommended).
    """
    app = FastAPI(title="scrollback", version=__version__)
    _store = store if store is not None else Store()
    _install_host_guard(app, allowed_hosts)

    # Translate unexpected source/IO failures (locked/corrupt DB, unreadable
    # files) into a clean 503 instead of a leaked 500 + traceback.
    import sqlite3

    from fastapi.responses import JSONResponse

    @app.exception_handler(sqlite3.Error)
    async def _sqlite_error(_request, exc):  # pragma: no cover - error path
        return JSONResponse(status_code=503, content={"detail": "data source unavailable"})

    @app.exception_handler(OSError)
    async def _os_error(_request, exc):  # pragma: no cover - error path
        return JSONResponse(status_code=503, content={"detail": "data source unavailable"})

    watchdog_on = idle_timeout > 0 and on_idle is not None
    if watchdog_on:
        _install_heartbeat_watchdog(app, on_idle, idle_timeout)
    else:
        # Always expose the config endpoint so the frontend can ask once and
        # skip heartbeats when auto-shutdown is not in effect.
        @app.get("/api/heartbeat-config")
        def heartbeat_config_off() -> dict[str, float]:
            return {"interval": 0.0, "enabled": 0.0}

    # -- API ---------------------------------------------------------------

    @app.get("/api/sources")
    def api_sources() -> list[dict[str, Any]]:
        # Report every KNOWN adapter, marking which have data on this machine.
        # The store holds the available ones; we additionally surface any
        # registered-but-unavailable adapters so the UI can show them greyed.
        from ..sources import registry

        out: list[dict[str, Any]] = []
        available_names = set()
        for s in _store.sources:
            available_names.add(s.name)
            out.append({
                "name": s.name,
                "label": s.label,
                "available": True,
                "location": str(s.location()) if s.location() else None,
            })
        for s in registry.all_sources():
            if s.name in available_names:
                continue
            out.append({
                "name": s.name,
                "label": s.label,
                "available": False,
                "location": None,
            })
        return out

    @app.get("/api/sessions")
    def api_sessions(
        source: str | None = None,
        dir: str | None = None,
        q: str | None = None,
        since: str | None = None,
        until: str | None = None,
        fold: bool = True,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=60, ge=1, le=2000),
    ) -> dict[str, Any]:
        if source and source not in {s.name for s in _store.sources}:
            raise HTTPException(status_code=400, detail=f"unknown source: {source}")
        st = _store.with_sources([source]) if source else _store
        # Fetch one extra to tell the client whether more pages exist.
        rows = st.list_sessions(
            directory=dir, query=q,
            since=_parse_dt(since), until=_parse_dt(until),
            offset=offset, limit=limit + 1, fold_subagents=fold,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "sessions": [session_summary(s) for s in rows],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    @app.get("/api/sessions/{source}/{session_id}")
    def api_session_detail(source: str, session_id: str) -> dict[str, Any]:
        """Full session including all messages. For very large sessions the
        frontend should prefer the meta + windowed messages endpoints."""
        sess = _store.load_session(session_id, source=source)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session_detail(sess)

    @app.get("/api/sessions/{source}/{session_id}/meta")
    def api_session_meta(source: str, session_id: str) -> dict[str, Any]:
        sess = _store.load_session_meta(session_id, source=source)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session_summary(sess)

    @app.get("/api/sessions/{source}/{session_id}/messages")
    def api_session_messages(
        source: str,
        session_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=40, ge=1, le=500),
    ) -> dict[str, Any]:
        msgs = _store.load_messages(
            session_id, source=source, offset=offset, limit=limit + 1
        )
        has_more = len(msgs) > limit
        msgs = msgs[:limit]
        return {
            "messages": [message_dict(m) for m in msgs],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    @app.get("/api/search")
    def api_search(
        q: str,
        dir: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = Query(default=100, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        if not q.strip():
            return []
        hits = _store.search(
            q, directory=dir, since=_parse_dt(since), until=_parse_dt(until), limit=limit
        )
        return [search_hit(h) for h in hits]

    @app.get("/api/export/{source}/{session_id}")
    def api_export(
        source: str,
        session_id: str,
        format: str = "markdown",
        reasoning: bool = True,
        tools: bool = True,
        math: str = "raw",
        download: bool = False,
    ) -> "Response":
        if format not in export.FORMATS:
            raise HTTPException(status_code=400, detail=f"bad format: {format}")
        if math not in export.MATH_MODES:
            raise HTTPException(status_code=400, detail=f"bad math mode: {math}")
        sess = _store.load_session(session_id, source=source)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        kwargs: dict[str, Any] = {}
        if format != "json":
            kwargs = {"include_reasoning": reasoning, "include_tools": tools, "math": math}
        body = export.render(sess, format, **kwargs)
        headers = {}
        if download:
            ext = _EXT.get(format, "txt")
            fname = f"{sess.source}_{sess.short_id}.{ext}"
            headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        return Response(content=body, media_type=_MEDIA.get(format, "text/plain"),
                        headers=headers)

    @app.get("/print/{source}/{session_id}")
    def print_view(
        source: str, session_id: str, reasoning: bool = True, tools: bool = True,
        math: str = "raw",
    ) -> "Response":
        """A print-friendly HTML page that auto-opens the print dialog.

        Used by the native-window 'print' action, which opens this URL in the
        user's real browser (where window.print() works)."""
        if math not in export.MATH_MODES:
            raise HTTPException(status_code=400, detail=f"bad math mode: {math}")
        sess = _store.load_session(session_id, source=source)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        html = export.to_html(sess, include_reasoning=reasoning, include_tools=tools, math=math)
        # Inject an auto-print trigger before </body>.
        auto = "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),300));</script>"
        if "</body>" in html:
            html = html.replace("</body>", auto + "</body>", 1)
        else:
            html += auto
        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.get("/api/stats")
    def api_stats(since: str | None = None, until: str | None = None) -> dict[str, Any]:
        """Aggregate usage statistics: per-source breakdown plus overall totals.

        Honours the same `since`/`until` window as the session list, so the
        stats page reflects the active date filters. Metadata-only (does not
        load message bodies), so it is cheap to compute on demand.
        """
        st = _store.stats(since=_parse_dt(since), until=_parse_dt(until))

        def _src_row(u) -> dict[str, Any]:
            return {
                "source": u.source,
                "sessions": u.sessions,
                "messages": u.messages,
                "tokens_input": u.tokens_input,
                "tokens_output": u.tokens_output,
                "tokens_cache_read": u.tokens_cache_read,
                "tokens_cache_write": u.tokens_cache_write,
                "tokens_reasoning": u.tokens_reasoning,
                "cost": u.cost,
            }

        # Sort by total token volume (in + out + cache), busiest first.
        rows = sorted(
            (_src_row(u) for u in st.per_source_usage.values()),
            key=lambda r: (r["tokens_input"] + r["tokens_output"]
                           + r["tokens_cache_read"] + r["tokens_cache_write"]),
            reverse=True,
        )
        return {
            "sessions": st.sessions,
            "messages": st.total_messages,
            "per_source": rows,
            "totals": {
                "tokens_input": st.total_tokens_input,
                "tokens_output": st.total_tokens_output,
                "tokens_cache_read": st.total_tokens_cache_read,
                "tokens_cache_write": st.total_tokens_cache_write,
                "tokens_reasoning": st.total_tokens_reasoning,
                "cost": st.total_cost,
            },
            "oldest": st.oldest.isoformat() if st.oldest else None,
            "newest": st.newest.isoformat() if st.newest else None,
        }

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__,
                "sources": [s.name for s in _store.sources]}

    # -- static frontend ---------------------------------------------------

    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _install_host_guard(app: "FastAPI", allowed_hosts: list[str] | None) -> None:
    """Reject requests whose Host header isn't an allowed hostname.

    Defends against DNS-rebinding: a malicious page can't point its own
    hostname at 127.0.0.1 and read local data, because the Host header would
    be that hostname, not a loopback name. The port portion is ignored (it
    can auto-change); only the hostname is checked.
    """
    if allowed_hosts is not None and not allowed_hosts:
        return  # explicitly disabled
    allow = set(_LOOPBACK_HOSTS)
    if allowed_hosts:
        allow.update(h.lower() for h in allowed_hosts)

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse

    class _HostGuard(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            host = request.headers.get("host", "")
            # Strip the port; handle IPv6 [::1]:port form.
            hostname = host.rsplit(":", 1)[0] if ":" in host and not host.endswith("]") else host
            hostname = hostname.strip("[]").lower()
            if hostname not in allow:
                return PlainTextResponse("Forbidden: unexpected Host header", status_code=403)
            return await call_next(request)

    app.add_middleware(_HostGuard)


def _install_heartbeat_watchdog(app: "FastAPI", on_idle, idle_timeout: float) -> None:
    """Auto-stop when the page stops sending heartbeats (window closed).

    The frontend POSTs /api/heartbeat on an interval. A background thread
    checks the last-seen time; if it exceeds `idle_timeout`, it calls
    `on_idle()` once. A grace period before the first heartbeat avoids
    shutting down during initial page load.
    """
    import threading
    import time

    state = {"last": time.monotonic() + max(idle_timeout, 10.0), "fired": False}

    @app.post("/api/heartbeat")
    def heartbeat() -> dict[str, str]:
        state["last"] = time.monotonic()
        return {"status": "ok"}

    @app.get("/api/heartbeat-config")
    def heartbeat_config() -> dict[str, float]:
        # Tell the client how often to ping (a third of the timeout).
        return {"interval": max(idle_timeout / 3.0, 2.0), "enabled": 1.0}

    def watch() -> None:
        while not state["fired"]:
            time.sleep(1.0)
            if time.monotonic() - state["last"] > idle_timeout:
                state["fired"] = True
                on_idle()
                return

    threading.Thread(target=watch, daemon=True).start()
