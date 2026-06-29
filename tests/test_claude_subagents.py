"""Tests for Claude Code subagent (sidechain) folding.

Builds a synthetic ~/.claude/projects layout in a temp dir so the test is
deterministic and independent of the developer's machine.
"""

import json
from pathlib import Path

import pytest

from agentscroll.sources.claudecode import ClaudeCodeSource


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _user(text, ts, sid, isMeta=False):
    return {"type": "user", "isMeta": isMeta, "timestamp": ts, "sessionId": sid,
            "cwd": "/proj", "message": {"role": "user", "content": text}}


def _assistant(text, ts, sid):
    return {"type": "assistant", "timestamp": ts, "sessionId": sid,
            "message": {"role": "assistant", "model": "claude-x",
                        "content": [{"type": "text", "text": text}]}}


@pytest.fixture
def claude_root(tmp_path):
    projects = tmp_path / "projects"
    proj = projects / "-proj"
    parent_id = "11111111-1111-1111-1111-111111111111"
    # parent transcript
    _write_jsonl(proj / f"{parent_id}.jsonl", [
        {"type": "ai-title", "aiTitle": "Parent session", "sessionId": parent_id},
        _user("do the thing", "2026-06-01T10:00:00Z", parent_id),
        _assistant("on it", "2026-06-01T10:00:05Z", parent_id),
    ])
    # subagent transcript + meta
    sub_dir = proj / parent_id / "subagents"
    _write_jsonl(sub_dir / "agent-abc123.jsonl", [
        _user("audit the code", "2026-05-15T09:00:00Z", parent_id),
        _assistant("found 3 issues", "2026-05-15T09:01:00Z", parent_id),
    ])
    (sub_dir / "agent-abc123.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "Code audit"})
    )
    return projects


def test_subagents_attached_as_children(claude_root):
    src = ClaudeCodeSource(root=claude_root)
    sessions = list(src.list_sessions())
    assert len(sessions) == 1
    parent = sessions[0]
    assert parent.title == "Parent session"
    assert len(parent.children) == 1
    child = parent.children[0]
    assert child.parent_id == parent.id
    assert child.agent == "Explore"
    assert "Code audit" in child.title
    assert "@Explore" in child.title
    assert child.message_count == 2


def test_child_session_resolves_and_loads(claude_root):
    src = ClaudeCodeSource(root=claude_root)
    parent = list(src.list_sessions())[0]
    child_id = parent.children[0].id

    # resolve_session_id should accept the synthetic child id
    assert src.resolve_session_id(child_id) == child_id

    # full load preserves the child id + parent link and loads messages
    full = src.load_session(child_id)
    assert full is not None
    assert full.id == child_id
    assert full.parent_id == parent.id
    assert len(full.messages) == 2
    assert "found 3 issues" in full.messages[1].text

    # windowed message load also works for children
    msgs = src.load_messages(child_id, offset=0, limit=1)
    assert len(msgs) == 1

    # meta-only load is message-free but keeps the child id
    meta = src.load_session_meta(child_id)
    assert meta.id == child_id
    assert meta.messages == ()


def test_parent_without_subagents_has_no_children(tmp_path):
    projects = tmp_path / "projects"
    sid = "22222222-2222-2222-2222-222222222222"
    _write_jsonl(projects / "-p" / f"{sid}.jsonl", [
        _user("hi", "2026-06-01T10:00:00Z", sid),
        _assistant("hello", "2026-06-01T10:00:01Z", sid),
    ])
    src = ClaudeCodeSource(root=projects)
    parent = list(src.list_sessions())[0]
    assert parent.children == ()
