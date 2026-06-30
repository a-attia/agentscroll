"""Tests for the Codex CLI source adapter (synthetic rollout files)."""

import json
from pathlib import Path

from scrollback.sources.codex import CodexSource


def _rollout(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "sessions" / "2025" / "01" / "31"
    d.mkdir(parents=True)
    f = d / "rollout-2025-01-31T12-34-56-abcd1234-ef56-7890-abcd-ef1234567890.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def _src(tmp_path: Path) -> CodexSource:
    return CodexSource(root=tmp_path / "sessions")


def test_codex_not_available_when_empty(tmp_path):
    assert CodexSource(root=tmp_path / "sessions").is_available() is False


def test_codex_lists_and_loads(tmp_path):
    _rollout(tmp_path, [
        {"type": "session_meta", "timestamp": "2025-01-31T12:34:56Z",
         "cwd": "/proj/foo", "model": "gpt-5-codex"},
        {"type": "response_item", "timestamp": "2025-01-31T12:35:00Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "fix the build"}]}},
        {"type": "response_item", "timestamp": "2025-01-31T12:35:05Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "On it."}]}},
        {"type": "response_item", "timestamp": "2025-01-31T12:35:06Z",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": {"command": "make"}}},
    ])
    src = _src(tmp_path)
    assert src.is_available()
    sessions = list(src.list_sessions())
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "codex"
    assert s.directory == "/proj/foo"
    assert s.model == "gpt-5-codex"
    assert s.title == "fix the build"

    full = src.load_session(s.id)
    assert full is not None
    roles = [m.role for m in full.messages]
    assert roles == ["user", "assistant", "assistant"]
    # the function_call becomes a tool part
    assert full.messages[2].parts[0].type == "tool"
    assert full.messages[2].parts[0].tool_name == "shell"


def test_codex_tolerates_flat_and_garbage_lines(tmp_path):
    # Older flat format + a malformed line that must be skipped.
    d = tmp_path / "sessions" / "2025" / "02" / "01"
    d.mkdir(parents=True)
    f = d / "rollout-2025-02-01T00-00-00-11112222-3333-4444-5555-666677778888.jsonl"
    f.write_text(
        json.dumps({"role": "user", "content": "hello", "timestamp": "2025-02-01T00:00:00Z"}) + "\n"
        + "{not valid json\n"
        + json.dumps({"role": "assistant", "content": "hi"}) + "\n"
    )
    src = _src(tmp_path)
    full = src.load_session(list(src.list_sessions())[0].id)
    assert [m.role for m in full.messages] == ["user", "assistant"]
