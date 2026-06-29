"""Claude Code source adapter (read-only JSONL).

Claude Code stores one directory per project under ~/.claude/projects/,
each containing one `<session-uuid>.jsonl` file per session (plus, for
subagents, `<uuid>` directories / sidechain files). Each line is a JSON
object with a top-level `type`. The lines we care about have
`type in {"user", "assistant"}` and carry a `message` object whose
`content` is either a plain string or a list of typed blocks
(text / thinking / tool_use / tool_result).

All reads are read-only file reads; we never modify the JSONL files.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..models import Message, Part, Session, _to_dt
from .base import Source

_DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def _env_root() -> Path:
    override = os.environ.get("AGENTSCROLL_CLAUDE_DIR")
    if override:
        p = Path(override).expanduser()
        # Accept either ~/.claude or ~/.claude/projects.
        return p / "projects" if (p / "projects").is_dir() else p
    return _DEFAULT_ROOT


class ClaudeCodeSource(Source):
    name = "claudecode"
    label = "Claude Code"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _env_root()

    # -- availability / location -------------------------------------------

    def is_available(self) -> bool:
        return self._root.is_dir()

    def location(self) -> Path | None:
        return self._root if self.is_available() else None

    # -- discovery ----------------------------------------------------------

    def _session_files(self) -> Iterator[Path]:
        # Top-level <uuid>.jsonl files are the primary sessions; nested
        # sidechain files are folded into their parent at load time, so we
        # list only top-level transcripts here.
        for project_dir in sorted(self._root.iterdir()):
            if not project_dir.is_dir():
                continue
            for f in sorted(project_dir.glob("*.jsonl")):
                yield f

    # -- listing ------------------------------------------------------------

    def list_sessions(self) -> Iterator[Session]:
        if not self.is_available():
            return iter(())
        return self._list_sessions()

    def _list_sessions(self) -> Iterator[Session]:
        for f in self._session_files():
            meta = _scan_metadata(f)
            if meta is None:
                continue
            yield Session(
                id=meta["session_id"],
                source=self.name,
                title=meta["title"],
                directory=meta["cwd"],
                created=_to_dt(meta["first_ts"]),
                updated=_to_dt(meta["last_ts"]),
                model=meta["model"],
                agent=None,
                parent_id=None,
                message_count=meta["msg_count"],
                raw={"path": str(f), "git_branch": meta["git_branch"]},
            )

    # -- single session -----------------------------------------------------

    def load_session(self, session_id: str) -> Session | None:
        if not self.is_available():
            return None
        path = self._find_path(session_id)
        if path is None:
            return None
        return _parse_session(path, self.name)

    def _find_path(self, session_id: str) -> Path | None:
        for f in self._session_files():
            if f.stem == session_id:
                return f
        # prefix match
        candidates = [f for f in self._session_files() if f.stem.startswith(session_id)]
        return candidates[0] if len(candidates) == 1 else None


# -- parsing helpers -------------------------------------------------------


def _iter_lines(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
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


def _scan_metadata(path: Path) -> dict[str, Any] | None:
    """Single pass over a transcript collecting just the metadata fields."""
    session_id = path.stem
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    title: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    msg_count = 0
    seen = False

    for obj in _iter_lines(path):
        seen = True
        t = obj.get("type")
        if obj.get("sessionId"):
            session_id = obj["sessionId"]
        if cwd is None and obj.get("cwd"):
            cwd = obj["cwd"]
        if git_branch is None and obj.get("gitBranch"):
            git_branch = obj["gitBranch"]
        if t == "ai-title":
            # Claude Code writes the title under `aiTitle` (newer) and may
            # also use `title`; the last one in the file wins.
            new_title = obj.get("aiTitle") or obj.get("title")
            if new_title:
                title = new_title
        if t in ("user", "assistant"):
            msg_count += 1
            ts = obj.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            if model is None:
                m = obj.get("message", {})
                if isinstance(m, dict) and m.get("model"):
                    model = m["model"]

    if not seen:
        return None
    return {
        "session_id": session_id,
        "cwd": cwd,
        "git_branch": git_branch,
        "model": model,
        "title": title or _fallback_title(path),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "msg_count": msg_count,
    }


def _fallback_title(path: Path) -> str:
    return path.stem[:8]


def _parse_session(path: Path, source_name: str) -> Session:
    meta = _scan_metadata(path) or {
        "session_id": path.stem,
        "cwd": None,
        "git_branch": None,
        "model": None,
        "title": _fallback_title(path),
        "first_ts": None,
        "last_ts": None,
        "msg_count": 0,
    }

    messages: list[Message] = []
    idx = 0
    for obj in _iter_lines(path):
        t = obj.get("type")
        if t not in ("user", "assistant"):
            continue
        if obj.get("isMeta"):
            # Skip local-command caveats and similar meta turns.
            continue
        m = obj.get("message", {})
        if not isinstance(m, dict):
            continue
        role = m.get("role", t)
        uuid = obj.get("uuid") or f"{path.stem}:{idx}"
        idx += 1
        parts = _content_to_parts(uuid, m.get("content"))
        if not parts:
            continue
        messages.append(
            Message(
                id=uuid,
                role=role,
                created=_to_dt(obj.get("timestamp")),
                parts=tuple(parts),
                model=m.get("model"),
                raw=obj,
            )
        )

    return Session(
        id=meta["session_id"],
        source=source_name,
        title=meta["title"],
        directory=meta["cwd"],
        created=_to_dt(meta["first_ts"]),
        updated=_to_dt(meta["last_ts"]),
        model=meta["model"],
        parent_id=None,
        message_count=len(messages),
        messages=tuple(messages),
        raw={"path": str(path), "git_branch": meta["git_branch"]},
    )


def _content_to_parts(msg_uuid: str, content: Any) -> list[Part]:
    """Normalize Claude Code message content into Parts."""
    parts: list[Part] = []
    if content is None:
        return parts
    if isinstance(content, str):
        if content.strip():
            parts.append(Part(id=f"{msg_uuid}:0", type="text", text=content))
        return parts
    if isinstance(content, list):
        for i, block in enumerate(content):
            part = _block_to_part(f"{msg_uuid}:{i}", block)
            if part is not None:
                parts.append(part)
    return parts


def _block_to_part(pid: str, block: Any) -> Part | None:
    if not isinstance(block, dict):
        if isinstance(block, str) and block.strip():
            return Part(id=pid, type="text", text=block)
        return None
    btype = block.get("type")
    if btype == "text":
        text = block.get("text", "")
        return Part(id=pid, type="text", text=text, raw=block) if text else None
    if btype == "thinking":
        text = block.get("thinking", "")
        return Part(id=pid, type="reasoning", text=text, raw=block) if text else None
    if btype == "tool_use":
        name = block.get("name")
        inp = block.get("input")
        text = f"$ {name} {json.dumps(inp, ensure_ascii=False)}" if inp is not None else f"$ {name}"
        return Part(id=pid, type="tool", text=text, tool_name=name, tool_status="call", raw=block)
    if btype == "tool_result":
        content = block.get("content")
        text = _stringify_tool_result(content)
        is_err = bool(block.get("is_error"))
        return Part(
            id=pid,
            type="tool",
            text=("[error] " + text) if is_err else text,
            tool_status="error" if is_err else "result",
            raw=block,
        )
    if btype == "image":
        return Part(id=pid, type="file", text="[image]", raw=block)
    return Part(id=pid, type="unknown", raw=block)


def _stringify_tool_result(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
            elif isinstance(b, str):
                out.append(b)
            else:
                out.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(out)
    return json.dumps(content, ensure_ascii=False)
