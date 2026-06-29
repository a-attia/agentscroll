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
        '    pip install "agentscroll[web]"\n'
        "or:\n"
        "    pip install fastapi uvicorn"
    ) from exc

from pathlib import Path

from .. import __version__, export
from ..serialize import search_hit, session_detail, session_summary
from ..store import Store

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


def create_app(store: Store | None = None) -> "FastAPI":
    """Build the FastAPI app. A custom Store can be injected for tests."""
    app = FastAPI(title="agentscroll", version=__version__)
    _store = store if store is not None else Store()

    # -- API ---------------------------------------------------------------

    @app.get("/api/sources")
    def api_sources() -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "label": s.label,
                "available": True,
                "location": str(s.location()) if s.location() else None,
            }
            for s in _store.sources
        ]

    @app.get("/api/sessions")
    def api_sessions(
        source: str | None = None,
        dir: str | None = None,
        q: str | None = None,
        limit: int = Query(default=200, ge=1, le=5000),
    ) -> list[dict[str, Any]]:
        st = _store.with_sources([source]) if source else _store
        sessions = st.list_sessions(directory=dir, query=q, limit=limit)
        return [session_summary(s) for s in sessions]

    @app.get("/api/sessions/{source}/{session_id}")
    def api_session_detail(source: str, session_id: str) -> dict[str, Any]:
        sess = _store.load_session(session_id, source=source)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session_detail(sess)

    @app.get("/api/search")
    def api_search(
        q: str,
        dir: str | None = None,
        limit: int = Query(default=100, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        if not q.strip():
            return []
        return [search_hit(h) for h in _store.search(q, directory=dir, limit=limit)]

    @app.get("/api/export/{source}/{session_id}")
    def api_export(
        source: str,
        session_id: str,
        format: str = "markdown",
        reasoning: bool = True,
        tools: bool = True,
        download: bool = False,
    ) -> "Response":
        if format not in export.FORMATS:
            raise HTTPException(status_code=400, detail=f"bad format: {format}")
        sess = _store.load_session(session_id, source=source)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        kwargs: dict[str, Any] = {}
        if format != "json":
            kwargs = {"include_reasoning": reasoning, "include_tools": tools}
        body = export.render(sess, format, **kwargs)
        headers = {}
        if download:
            ext = _EXT.get(format, "txt")
            fname = f"{sess.source}_{sess.short_id}.{ext}"
            headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        return Response(content=body, media_type=_MEDIA.get(format, "text/plain"),
                        headers=headers)

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__,
                "sources": [s.name for s in _store.sources]}

    # -- static frontend ---------------------------------------------------

    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app
