"""Aider source adapter (read-only Markdown chat logs).

Aider records its conversation per project in a Markdown file at the repo
root::

    <project>/.aider.chat.history.md

The file accumulates across runs. Aider delimits each run with a line::

    # aider chat started at 2025-01-31 12:34:56

Within a run, user turns are written as level-4 headings (``#### <text>``)
and assistant replies as the prose that follows. We treat each "chat
started at" block as one session, so a project's history file yields
multiple sessions ordered by start time.

Because these files are scattered one-per-project rather than in a single
well-known directory, the adapter searches a configurable set of roots
(``AGENTSCROLL_AIDER_DIRS``, colon-separated) and otherwise the current
working directory tree (depth-limited). Set the env var to your projects
parent directory to index everything.

NOTE: written to Aider's documented log format; not verified against a live
.aider.chat.history.md on the development machine. Parsing is tolerant.

All reads are read-only.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from ..models import Message, Part, Session
from .base import Source

_HISTORY_NAME = ".aider.chat.history.md"
_STARTED_RE = re.compile(r"^#+\s*aider chat started at\s+(?P<ts>.+?)\s*$", re.IGNORECASE)
_USER_RE = re.compile(r"^####\s+(?P<text>.*)$")
_MAX_DEPTH = 6


def _search_roots() -> list[Path]:
    env = os.environ.get("AGENTSCROLL_AIDER_DIRS")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    return [Path.cwd()]


def _find_history_files(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            # A direct path to a history file is also accepted.
            if root.name == _HISTORY_NAME and root.is_file():
                found.append(root)
            continue
        # Depth-limited walk to avoid scanning an entire home directory.
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            if depth > _MAX_DEPTH:
                dirnames[:] = []
                continue
            # Skip common heavy/irrelevant dirs.
            dirnames[:] = [d for d in dirnames
                           if d not in {".git", "node_modules", ".venv", "venv", "__pycache__"}]
            if _HISTORY_NAME in filenames:
                found.append(Path(dirpath) / _HISTORY_NAME)
    return sorted(set(found))


class AiderSource(Source):
    name = "aider"
    label = "Aider"

    def __init__(self, roots: list[Path] | None = None) -> None:
        self._roots = roots if roots is not None else _search_roots()

    # -- availability / location -------------------------------------------

    def is_available(self) -> bool:
        return bool(_find_history_files(self._roots))

    def location(self) -> Path | None:
        files = _find_history_files(self._roots)
        # Report the common parent (or the single file's dir) for diagnostics.
        return files[0].parent if files else None

    # -- discovery ----------------------------------------------------------

    def list_sessions(self) -> Iterator[Session]:
        for f in _find_history_files(self._roots):
            project = f.parent
            for blk in _split_sessions(f):
                yield _session_from_block(blk, project, f, with_messages=False)

    def load_session(self, session_id: str) -> Session | None:
        for f in _find_history_files(self._roots):
            project = f.parent
            for blk in _split_sessions(f):
                sid = _session_id(f, blk["start_raw"], blk["index"])
                if sid == session_id or sid.startswith(session_id):
                    return _session_from_block(blk, project, f, with_messages=True)
        return None


# -- parsing helpers -------------------------------------------------------


def _session_id(path: Path, start_raw: str, index: int) -> str:
    h = hashlib.sha1(f"{path}|{start_raw}|{index}".encode()).hexdigest()[:12]
    return f"aider-{h}"


def _parse_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _split_sessions(path: Path) -> list[dict]:
    """Split a history file into per-run blocks by 'chat started at' lines."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    blocks: list[dict] = []
    current: dict | None = None
    for line in lines:
        m = _STARTED_RE.match(line)
        if m:
            if current is not None:
                blocks.append(current)
            current = {"start_raw": m.group("ts"), "lines": [], "index": len(blocks)}
            continue
        if current is None:
            # Content before the first marker -> implicit first block.
            current = {"start_raw": "", "lines": [], "index": 0}
        current["lines"].append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _block_messages(block: dict) -> list[Message]:
    """Turn a run-block's lines into user/assistant messages.

    Level-4 headings start a user turn; the prose until the next heading is
    the assistant reply.
    """
    messages: list[Message] = []
    idx = 0
    role: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal idx, buf, role
        if role and buf:
            text = "\n".join(buf).strip()
            if text:
                messages.append(Message(
                    id=f"{block['index']}:{idx}", role=role, created=None,
                    parts=(Part(id=f"{block['index']}:{idx}:0", type="text", text=text),),
                ))
                idx += 1
        buf = []

    for line in block["lines"]:
        m = _USER_RE.match(line)
        if m:
            flush()
            role = "user"
            buf = [m.group("text")]
            flush()
            role = "assistant"
            continue
        buf.append(line)
    flush()
    return messages


def _session_from_block(block: dict, project: Path, path: Path, *, with_messages: bool) -> Session:
    start = _parse_ts(block["start_raw"])
    msgs = _block_messages(block)
    first_user = next((m.text for m in msgs if m.role == "user" and m.text), "")
    title = " ".join(first_user.split())[:60] if first_user else project.name
    return Session(
        id=_session_id(path, block["start_raw"], block["index"]),
        source=AiderSource.name,
        title=title or project.name,
        directory=str(project),
        created=start,
        updated=start,
        message_count=len(msgs),
        messages=tuple(msgs) if with_messages else (),
        raw={"path": str(path)},
    )
