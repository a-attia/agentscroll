"""Tests for the Claude Code message-paging (byte-offset) index.

Verifies that windowed load_messages is correct (matches a full load),
handles offsets/limits and meta/empty lines, and stays consistent after the
file changes (cache invalidation by mtime+size).
"""

import json
from pathlib import Path

from agentscroll.sources import claudecode
from agentscroll.sources.claudecode import ClaudeCodeSource


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _msg(role, text, i, sid, isMeta=False):
    base = {"type": role, "timestamp": f"2026-06-01T10:{i:02d}:00Z",
            "sessionId": sid, "uuid": f"{role}-{i}"}
    if role == "user":
        base["isMeta"] = isMeta
        base["cwd"] = "/proj"
        base["message"] = {"role": "user", "content": text}
    else:
        base["message"] = {"role": "assistant", "model": "m",
                            "content": [{"type": "text", "text": text}]}
    return base


def _make_session(tmp_path, n=30):
    sid = "33333333-3333-3333-3333-333333333333"
    rows = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append(_msg(role, f"message number {i}", i, sid))
    # interleave a meta line and an empty-content line that must be skipped
    rows.insert(3, _msg("user", "", 99, sid, isMeta=True))
    _write_jsonl(tmp_path / "projects" / "-proj" / f"{sid}.jsonl", rows)
    return tmp_path / "projects", sid, n


def test_paging_matches_full_load(tmp_path):
    claudecode._OFFSET_CACHE.clear()
    projects, sid, n = _make_session(tmp_path, 30)
    src = ClaudeCodeSource(root=projects)
    full = src.load_session(sid).messages
    # Page through in windows of 7 and concatenate.
    paged = []
    off = 0
    while True:
        page = src.load_messages(sid, offset=off, limit=7)
        if not page:
            break
        paged += page
        off += 7
    assert [m.id for m in full] == [m.id for m in paged]
    assert len(full) == n  # meta + empty lines excluded


def test_deep_offset(tmp_path):
    claudecode._OFFSET_CACHE.clear()
    projects, sid, n = _make_session(tmp_path, 50)
    src = ClaudeCodeSource(root=projects)
    page = src.load_messages(sid, offset=40, limit=5)
    assert [m.id for m in page] == [f"{'user' if i%2==0 else 'assistant'}-{i}"
                                    for i in range(40, 45)]


def test_offset_index_invalidates_on_change(tmp_path):
    claudecode._OFFSET_CACHE.clear()
    projects, sid, _ = _make_session(tmp_path, 10)
    src = ClaudeCodeSource(root=projects)
    assert len(src.load_messages(sid, offset=0, limit=100)) == 10
    # Append two more messages; the index must pick them up (mtime/size change).
    f = projects / "-proj" / f"{sid}.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_msg("user", "extra a", 100, sid)) + "\n")
        fh.write(json.dumps(_msg("assistant", "extra b", 101, sid)) + "\n")
    assert len(src.load_messages(sid, offset=0, limit=100)) == 12
