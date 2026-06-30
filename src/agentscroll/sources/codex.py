"""Codex CLI source adapter (read-only JSONL rollouts).

Codex (OpenAI's terminal coding agent) records each session as a "rollout"
JSONL file under::

    ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<uuid>.jsonl

Each line is a JSON event. The first line is typically a session-meta
record (``{"type": "session_meta", ...}`` or a payload carrying ``id`` /
``cwd`` / ``timestamp`` / ``instructions``); subsequent lines are response
items, of which the conversational ones look like::

    {"type": "response_item", "payload": {"type": "message",
        "role": "user"|"assistant", "content": [{"type": "input_text"|
        "output_text", "text": "..."}]}}

and tool/function calls appear as ``function_call`` / ``local_shell_call``
payloads. The exact shape has evolved across Codex versions, so this parser
is intentionally tolerant: it pulls role + text from whatever message-like
records it recognizes and skips the rest.

NOTE: this adapter is written to the documented/observed Codex format but
has not been verified against a live ~/.codex/sessions store on the
development machine; field handling is deliberately defensive.

All reads are read-only file reads; the rollout files are never modified.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import Message, Part, Session, _to_dt
from .base import Source

_DEFAULT_ROOT = Path.home() / ".codex" / "sessions"

# rollout-2025-01-31T12-34-56-<uuid>.jsonl  ->  capture the uuid tail.
_ROLLOUT_RE = re.compile(r"rollout-(?P<ts>[\dT:-]+)-(?P<uuid>[0-9a-fA-F-]{8,})\.jsonl$")


def _env_root() -> Path:
    override = os.environ.get("AGENTSCROLL_CODEX_DIR")
    if override:
        p = Path(override).expanduser()
        return p / "sessions" if (p / "sessions").is_dir() else p
    return _DEFAULT_ROOT


class CodexSource(Source):
    name = "codex"
    label = "Codex"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _env_root()

    def resume_command(self, session) -> str | None:
        # Codex resumes a recorded session with `codex resume <id>` (documented;
        # not verified on this machine). Run from the session directory.
        import shlex

        cmd = f"codex resume {session.id}"
        if session.directory:
            return f"cd {shlex.quote(session.directory)} && {cmd}"
        return cmd

    # -- availability / location -------------------------------------------

    def is_available(self) -> bool:
        return self._root.is_dir()

    def location(self) -> Path | None:
        return self._root if self.is_available() else None

    # -- discovery ----------------------------------------------------------

    def _rollout_files(self) -> Iterator[Path]:
        # Rollouts are nested under YYYY/MM/DD; rglob keeps us version-proof
        # against minor layout changes.
        yield from sorted(self._root.rglob("rollout-*.jsonl"))

    def _session_id_for(self, path: Path) -> str:
        m = _ROLLOUT_RE.search(path.name)
        return m.group("uuid") if m else path.stem

    def _find_path(self, session_id: str) -> Path | None:
        for f in self._rollout_files():
            if self._session_id_for(f) == session_id or f.stem == session_id:
                return f
        cands = [f for f in self._rollout_files() if self._session_id_for(f).startswith(session_id)]
        return cands[0] if len(cands) == 1 else None

    # -- listing ------------------------------------------------------------

    def list_sessions(self) -> Iterator[Session]:
        if not self.is_available():
            return iter(())
        return self._list_sessions()

    def _list_sessions(self) -> Iterator[Session]:
        for f in self._rollout_files():
            meta = _scan_meta(f)
            if meta is None:
                continue
            yield Session(
                id=self._session_id_for(f),
                source=self.name,
                title=meta["title"],
                directory=meta["cwd"],
                created=_to_dt(meta["first_ts"]),
                updated=_to_dt(meta["last_ts"]),
                model=meta["model"],
                message_count=meta["msg_count"],
                raw={"path": str(f)},
            )

    # -- single session -----------------------------------------------------

    def load_session(self, session_id: str) -> Session | None:
        if not self.is_available():
            return None
        path = self._find_path(session_id)
        if path is None:
            return None
        meta = _scan_meta(path) or _empty_meta(path)
        messages = list(_iter_messages(path))
        return Session(
            id=self._session_id_for(path),
            source=self.name,
            title=meta["title"],
            directory=meta["cwd"],
            created=_to_dt(meta["first_ts"]),
            updated=_to_dt(meta["last_ts"]),
            model=meta["model"],
            message_count=len(messages),
            messages=tuple(messages),
            raw={"path": str(path)},
        )


# -- parsing helpers -------------------------------------------------------


def _iter_lines(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _payload(obj: dict[str, Any]) -> dict[str, Any]:
    """Codex wraps records in {'type':..., 'payload': {...}} in newer
    versions; older ones are flat. Return the inner dict either way."""
    p = obj.get("payload")
    return p if isinstance(p, dict) else obj


def _record_text(p: dict[str, Any]) -> str:
    """Extract human-readable text from a message-like payload."""
    content = p.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("input_text") or block.get("output_text")
                if t:
                    out.append(t)
            elif isinstance(block, str):
                out.append(block)
        return "\n".join(out)
    # some versions use a flat "text"
    return p.get("text") or ""


def _scan_meta(path: Path) -> dict[str, Any] | None:
    cwd: str | None = None
    model: str | None = None
    title: str | None = None
    first_ts: str | int | None = None
    last_ts: str | int | None = None
    msg_count = 0
    seen = False

    for obj in _iter_lines(path):
        seen = True
        ts = obj.get("timestamp") or obj.get("ts")
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
        p = _payload(obj)
        if cwd is None:
            cwd = obj.get("cwd") or p.get("cwd")
        if model is None:
            model = obj.get("model") or p.get("model")
        role = p.get("role")
        ptype = p.get("type") or obj.get("type")
        if role in ("user", "assistant") or ptype == "message":
            msg_count += 1
            if title is None and role == "user":
                txt = _record_text(p).strip()
                if txt and not txt.startswith("<"):
                    title = " ".join(txt.split())[:60]

    if not seen:
        return None
    if cwd and not title:
        title = Path(cwd).name
    return {
        "cwd": cwd,
        "model": model,
        "title": title or path.stem[:16],
        "first_ts": first_ts,
        "last_ts": last_ts,
        "msg_count": msg_count,
    }


def _empty_meta(path: Path) -> dict[str, Any]:
    return {"cwd": None, "model": None, "title": path.stem[:16],
            "first_ts": None, "last_ts": None, "msg_count": 0}


def _iter_messages(path: Path) -> Iterator[Message]:
    idx = 0
    for obj in _iter_lines(path):
        p = _payload(obj)
        role = p.get("role")
        ptype = p.get("type") or obj.get("type")
        created = _to_dt(obj.get("timestamp") or obj.get("ts"))

        if role in ("user", "assistant"):
            text = _record_text(p)
            if not text.strip():
                continue
            yield Message(
                id=f"{path.stem}:{idx}",
                role=role,
                created=created,
                parts=(Part(id=f"{path.stem}:{idx}:0", type="text", text=text),),
                model=p.get("model"),
                raw=obj,
            )
            idx += 1
        elif ptype in ("function_call", "local_shell_call", "tool_call"):
            name = p.get("name") or p.get("tool") or "tool"
            args = p.get("arguments") or p.get("input") or p.get("command")
            text = f"$ {name} {json.dumps(args, ensure_ascii=False)}" if args is not None else f"$ {name}"
            yield Message(
                id=f"{path.stem}:{idx}",
                role="assistant",
                created=created,
                parts=(Part(id=f"{path.stem}:{idx}:0", type="tool", text=text,
                            tool_name=name, tool_status="call"),),
                raw=obj,
            )
            idx += 1


def _now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
