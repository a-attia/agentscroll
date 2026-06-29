"""opencode source adapter (read-only SQLite).

opencode stores sessions in a SQLite database (default
~/.local/share/opencode/opencode.db) with three relevant tables:

  session(id, title, directory, time_created, time_updated, model, agent,
          parent_id, ...)
  message(id, session_id, time_created, data)   -- data is JSON
  part(id, message_id, session_id, time_created, data)  -- data is JSON

We open the database strictly read-only (URI `mode=ro`) so we never lock
it for writing or interfere with a running opencode. The DB may be large
and live (WAL active); read-only queries are safe and see a consistent
snapshot.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..models import Message, Part, Session, _to_dt
from .base import Source

_DEFAULT_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

# Map opencode part `type` values to our PartType, with a renderer each.
_TEXT_PART_TYPES = {"text", "reasoning"}


def _env_db() -> Path:
    override = os.environ.get("AGENTSCROLL_OPENCODE_DB")
    return Path(override).expanduser() if override else _DEFAULT_DB


class OpenCodeSource(Source):
    name = "opencode"
    label = "opencode"

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _env_db()

    # -- availability / location -------------------------------------------

    def is_available(self) -> bool:
        return self._db_path.is_file()

    def location(self) -> Path | None:
        return self._db_path if self.is_available() else None

    # -- read-only connection ----------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # `mode=ro` => read-only; never creates or writes. `immutable=0`
        # so SQLite still consults the WAL for a consistent live snapshot.
        uri = f"file:{self._db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    # -- listing ------------------------------------------------------------

    def list_sessions(self) -> Iterator[Session]:
        if not self.is_available():
            return iter(())
        return self._list_sessions()

    def _list_sessions(self) -> Iterator[Session]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.directory, s.time_created,
                       s.time_updated, s.model, s.agent, s.parent_id,
                       (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id)
                           AS msg_count
                FROM session s
                ORDER BY s.time_updated DESC
                """
            ).fetchall()
        for r in rows:
            yield Session(
                id=r["id"],
                source=self.name,
                title=r["title"] or "(untitled)",
                directory=r["directory"],
                created=_to_dt(r["time_created"]),
                updated=_to_dt(r["time_updated"]),
                model=_parse_model(r["model"]),
                agent=r["agent"],
                parent_id=r["parent_id"],
                message_count=r["msg_count"],
            )

    # -- single session -----------------------------------------------------

    def load_session(self, session_id: str) -> Session | None:
        if not self.is_available():
            return None
        with self._connect() as conn:
            srow = conn.execute(
                "SELECT * FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            if srow is None:
                return None
            mrows = conn.execute(
                """
                SELECT id, time_created, data FROM message
                WHERE session_id = ?
                ORDER BY time_created, id
                """,
                (session_id,),
            ).fetchall()
            prows = conn.execute(
                """
                SELECT id, message_id, time_created, data FROM part
                WHERE session_id = ?
                ORDER BY time_created, id
                """,
                (session_id,),
            ).fetchall()

        parts_by_message: dict[str, list[Part]] = {}
        for pr in prows:
            data = _loads(pr["data"])
            part = _to_part(pr["id"], data)
            if part is None:
                continue
            parts_by_message.setdefault(pr["message_id"], []).append(part)

        messages: list[Message] = []
        for mr in mrows:
            data = _loads(mr["data"])
            messages.append(
                Message(
                    id=mr["id"],
                    role=data.get("role", "assistant"),
                    created=_to_dt(mr["time_created"]),
                    parts=tuple(parts_by_message.get(mr["id"], ())),
                    model=_model_from_message(data),
                    raw=data,
                )
            )

        return Session(
            id=srow["id"],
            source=self.name,
            title=srow["title"] or "(untitled)",
            directory=srow["directory"],
            created=_to_dt(srow["time_created"]),
            updated=_to_dt(srow["time_updated"]),
            model=_parse_model(srow["model"]),
            agent=srow["agent"],
            parent_id=srow["parent_id"],
            message_count=len(messages),
            messages=tuple(messages),
        )


# -- helpers ---------------------------------------------------------------


def _loads(s: str | None) -> dict[str, Any]:
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_model(model_field: str | None) -> str | None:
    """session.model is JSON like {"id": "...", "providerID": "..."}."""
    if not model_field:
        return None
    try:
        obj = json.loads(model_field)
        if isinstance(obj, dict):
            return obj.get("id") or obj.get("modelID")
    except (json.JSONDecodeError, TypeError):
        pass
    return model_field


def _model_from_message(data: dict[str, Any]) -> str | None:
    m = data.get("model")
    if isinstance(m, dict):
        return m.get("modelID") or m.get("id")
    return data.get("modelID")


def _to_part(part_id: str, data: dict[str, Any]) -> Part | None:
    ptype = data.get("type", "unknown")
    if ptype in _TEXT_PART_TYPES:
        return Part(
            id=part_id,
            type=ptype,
            text=data.get("text", "") or "",
            raw=data,
        )
    if ptype == "tool":
        state = data.get("state", {}) or {}
        tool_name = data.get("tool")
        status = state.get("status")
        text = _render_tool(tool_name, state)
        return Part(
            id=part_id,
            type="tool",
            text=text,
            tool_name=tool_name,
            tool_status=status,
            raw=data,
        )
    # Other part types (step-start/step-finish/patch/file/compaction) are
    # kept with empty text; export/search can opt in via raw.
    return Part(id=part_id, type=ptype if ptype in _KNOWN else "unknown", raw=data)


_KNOWN = {
    "text",
    "reasoning",
    "tool",
    "file",
    "patch",
    "step-start",
    "step-finish",
    "compaction",
}


def _render_tool(tool_name: str | None, state: dict[str, Any]) -> str:
    """A compact, searchable rendering of a tool call and its result."""
    parts: list[str] = []
    inp = state.get("input")
    if inp is not None:
        parts.append(f"$ {tool_name} {json.dumps(inp, ensure_ascii=False)}")
    out = state.get("output")
    if isinstance(out, str) and out:
        parts.append(out)
    elif out is not None:
        parts.append(json.dumps(out, ensure_ascii=False))
    err = state.get("error")
    if err:
        parts.append(f"[error] {err}")
    return "\n".join(parts)
