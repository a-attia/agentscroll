"""Usage/token accounting across adapters, stats, and export.

Covers the input/output/cache-read/cache-write/reasoning fields end to end:
each adapter parses what its format exposes, sessions with no usage stay
None (not a misleading 0), Store.stats() sums them, and the export/summary
surfaces render them.
"""

import json
import sqlite3
from pathlib import Path

from scrollback import export
from scrollback.models import Message, Part, Session
from scrollback.sources.claudecode import ClaudeCodeSource
from scrollback.sources.codex import CodexSource
from scrollback.sources.opencode import OpenCodeSource
from scrollback.store import Store


# -- opencode (SQLite columns) --------------------------------------------

def _make_opencode_db(path: Path, *, with_cache_cols: bool) -> None:
    conn = sqlite3.connect(path)
    cache_cols = (
        ", tokens_cache_read INTEGER, tokens_cache_write INTEGER, "
        "tokens_reasoning INTEGER"
        if with_cache_cols else ""
    )
    conn.executescript(
        f"""
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT,
            directory TEXT, title TEXT, time_created INTEGER,
            time_updated INTEGER, agent TEXT, model TEXT,
            cost REAL, tokens_input INTEGER, tokens_output INTEGER{cache_cols}
        );
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
            time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT,
            session_id TEXT, time_created INTEGER, data TEXT);
        """
    )
    if with_cache_cols:
        conn.execute(
            "INSERT INTO session (id, title, time_created, time_updated, cost, "
            "tokens_input, tokens_output, tokens_cache_read, tokens_cache_write, "
            "tokens_reasoning) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("s1", "Heat eqn", 1000, 2000, 0.42, 1500, 3000, 250000, 40000, 800),
        )
    else:
        conn.execute(
            "INSERT INTO session (id, title, time_created, time_updated, cost, "
            "tokens_input, tokens_output) VALUES (?,?,?,?,?,?,?)",
            ("s1", "Heat eqn", 1000, 2000, 0.42, 1500, 3000),
        )
    conn.commit()
    conn.close()


def test_opencode_reads_cache_columns(tmp_path):
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, with_cache_cols=True)
    src = OpenCodeSource(db_path=db)

    listed = next(iter(src.list_sessions()))
    assert listed.tokens_input == 1500
    assert listed.tokens_output == 3000
    assert listed.tokens_cache_read == 250000
    assert listed.tokens_cache_write == 40000
    assert listed.tokens_reasoning == 800

    full = src.load_session("s1")
    assert full.tokens_cache_read == 250000
    assert full.tokens_cache_write == 40000


def test_opencode_tolerates_db_without_cache_columns(tmp_path):
    # Older opencode DBs lack the cache columns; the adapter must not crash and
    # should report them as None.
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, with_cache_cols=False)
    src = OpenCodeSource(db_path=db)

    listed = next(iter(src.list_sessions()))
    assert listed.tokens_input == 1500
    assert listed.tokens_cache_read is None
    assert listed.tokens_cache_write is None
    assert listed.tokens_reasoning is None


# -- Claude Code (per-turn usage in JSONL) --------------------------------

def _cc_assistant(text, ts, sid, usage=None):
    msg = {"role": "assistant", "model": "claude-x",
           "content": [{"type": "text", "text": text}]}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": ts, "sessionId": sid, "message": msg}


def _cc_user(text, ts, sid):
    return {"type": "user", "timestamp": ts, "sessionId": sid, "cwd": "/proj",
            "message": {"role": "user", "content": text}}


def test_claudecode_sums_per_turn_usage(tmp_path):
    root = tmp_path / "projects"   # adapter root is the `projects/` dir
    sid = "aaaaaaaa-1111-2222-3333-444444444444"
    f = root / "-proj" / f"{sid}.jsonl"
    f.parent.mkdir(parents=True)
    rows = [
        _cc_user("q1", "2026-03-14T09:30:00Z", sid),
        _cc_assistant("a1", "2026-03-14T09:30:05Z", sid, usage={
            "input_tokens": 100, "output_tokens": 200,
            "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 300,
        }),
        _cc_user("q2", "2026-03-14T09:31:00Z", sid),
        _cc_assistant("a2", "2026-03-14T09:31:05Z", sid, usage={
            "input_tokens": 50, "output_tokens": 400,
            "cache_read_input_tokens": 8000, "cache_creation_input_tokens": 100,
        }),
    ]
    with f.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    src = ClaudeCodeSource(root=root)
    s = next(iter(src.list_sessions()))
    assert s.tokens_input == 150       # 100 + 50
    assert s.tokens_output == 600      # 200 + 400
    assert s.tokens_cache_read == 13000  # 5000 + 8000
    assert s.tokens_cache_write == 400   # 300 + 100

    # load_session path agrees with the listing path.
    full = src.load_session(s.id)
    assert full.tokens_cache_read == 13000


def test_claudecode_usage_none_when_absent(tmp_path):
    root = tmp_path / "projects"
    sid = "bbbbbbbb-1111-2222-3333-444444444444"
    f = root / "-proj" / f"{sid}.jsonl"
    f.parent.mkdir(parents=True)
    with f.open("w") as fh:
        fh.write(json.dumps(_cc_user("hi", "2026-03-14T09:30:00Z", sid)) + "\n")
        fh.write(json.dumps(_cc_assistant("yo", "2026-03-14T09:30:05Z", sid)) + "\n")

    s = next(iter(ClaudeCodeSource(root=root).list_sessions()))
    assert s.tokens_input is None
    assert s.tokens_cache_read is None


# -- Codex (best-effort; None when the format carries no usage) ------------

def _codex_rollout(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "sessions" / "2025" / "01" / "31"
    d.mkdir(parents=True)
    f = d / "rollout-2025-01-31T12-34-56-abcd1234-ef56-7890-abcd-ef1234567890.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return f


def test_codex_usage_none_without_token_records(tmp_path):
    _codex_rollout(tmp_path, [
        {"type": "session_meta", "timestamp": "2025-01-31T12:34:56Z",
         "cwd": "/proj", "model": "gpt-5-codex"},
        {"type": "response_item", "timestamp": "2025-01-31T12:35:00Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "hi"}]}},
    ])
    s = next(iter(CodexSource(root=tmp_path / "sessions").list_sessions()))
    assert s.tokens_input is None
    assert s.tokens_cache_read is None


def test_codex_parses_token_count_when_present(tmp_path):
    _codex_rollout(tmp_path, [
        {"type": "session_meta", "timestamp": "2025-01-31T12:34:56Z",
         "cwd": "/proj", "model": "gpt-5-codex"},
        {"type": "response_item", "timestamp": "2025-01-31T12:35:00Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "hi"}]}},
        {"type": "event_msg", "timestamp": "2025-01-31T12:35:10Z",
         "payload": {"type": "token_count", "input_tokens": 900,
                     "output_tokens": 120, "cached_input_tokens": 4000}},
    ])
    s = next(iter(CodexSource(root=tmp_path / "sessions").list_sessions()))
    assert s.tokens_input == 900
    assert s.tokens_output == 120
    assert s.tokens_cache_read == 4000


# -- stats aggregation -----------------------------------------------------

class _FakeSource:
    name = "fake"
    label = "Fake"

    def __init__(self, sessions):
        self._s = sessions

    def is_available(self):
        return True

    def location(self):
        return Path("/tmp/fake")

    def list_sessions(self):
        return iter(self._s)

    def load_session(self, sid):
        return next((s for s in self._s if s.id == sid), None)


def _sess(sid, *, source="fake", **usage):
    from datetime import datetime, timezone
    dt = datetime(2026, 3, 14, tzinfo=timezone.utc)
    return Session(id=sid, source=source, title=sid, directory=None,
                   created=dt, updated=dt, message_count=1, **usage)


def test_stats_sums_cache_and_reasoning():
    store = Store([_FakeSource([
        _sess("a", tokens_input=100, tokens_output=200,
              tokens_cache_read=1000, tokens_cache_write=50, tokens_reasoning=10),
        _sess("b", tokens_input=5, tokens_output=7,
              tokens_cache_read=2000, tokens_cache_write=25, tokens_reasoning=3),
        _sess("c"),  # no usage -> contributes nothing
    ])])
    st = store.stats()
    assert st.total_tokens_input == 105
    assert st.total_tokens_output == 207
    assert st.total_tokens_cache_read == 3000
    assert st.total_tokens_cache_write == 75
    assert st.total_tokens_reasoning == 13


class _NamedSource(_FakeSource):
    def __init__(self, name, sessions):
        super().__init__(sessions)
        self.name = name


def test_stats_per_source_usage_breakdown():
    # Two tools, each with usage; one reports cost, the other does not.
    oc = _NamedSource("opencode", [
        _sess("o1", source="opencode", tokens_input=100, tokens_output=200,
              tokens_cache_read=5000, tokens_cache_write=300, cost=0.40),
        _sess("o2", source="opencode", tokens_input=10, tokens_output=20, cost=0.02),
    ])
    cc = _NamedSource("claudecode", [
        _sess("c1", source="claudecode", tokens_input=50, tokens_output=90,
              tokens_cache_read=8000),
    ])
    st = Store([oc, cc]).stats()

    assert set(st.per_source_usage) == {"opencode", "claudecode"}
    o = st.per_source_usage["opencode"]
    assert o.sessions == 2
    assert o.tokens_input == 110
    assert o.tokens_cache_read == 5000
    assert abs(o.cost - 0.42) < 1e-9

    c = st.per_source_usage["claudecode"]
    assert c.sessions == 1
    assert c.tokens_cache_read == 8000
    # Claude Code reported no cost -> None (not a misleading 0.0).
    assert c.cost is None

    # Per-source sums roll up to the overall totals.
    assert st.total_tokens_input == 160
    assert st.total_tokens_cache_read == 13000


def test_stats_real_zero_cost_is_kept_distinct_from_none():
    # A source that reports a genuine $0.00 (free/local model) must show cost
    # 0.0, NOT None -- the "unknown vs real zero" distinction the SourceUsage
    # docstring promises. (Regression: a truthiness check dropped real zeros.)
    free = _NamedSource("opencode", [
        _sess("z1", source="opencode", tokens_input=10, tokens_output=5, cost=0.0),
    ])
    none = _NamedSource("claudecode", [
        _sess("n1", source="claudecode", tokens_input=10, tokens_output=5),
    ])
    st = Store([free, none]).stats()
    assert st.per_source_usage["opencode"].cost == 0.0    # known, real zero
    assert st.per_source_usage["claudecode"].cost is None  # unknown


# -- export surfacing ------------------------------------------------------

def _msg():
    return Message(id="m", role="assistant", created=None,
                   parts=(Part(id="p", type="text", text="hi"),))


def test_export_usage_summary_in_markdown_and_html():
    s = Session(id="s", source="opencode", title="t", directory=None,
                created=None, updated=None, messages=(_msg(),), message_count=1,
                tokens_input=1500, tokens_output=3000,
                tokens_cache_read=250000, tokens_cache_write=40000, cost=0.42)
    md = export.to_markdown(s)
    assert "**Usage**" in md
    assert "1.5k in / 3.0k out" in md
    assert "cache 250.0k read / 40.0k write" in md
    assert "$0.42" in md

    html = export.to_html(s)
    assert "usage:" in html
    assert "250.0k read" in html


def test_export_no_usage_line_when_absent():
    s = Session(id="s", source="aider", title="t", directory=None,
                created=None, updated=None, messages=(_msg(),), message_count=1)
    assert "**Usage**" not in export.to_markdown(s)
    assert "usage:" not in export.to_html(s)
