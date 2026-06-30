"""Tests for the optional FTS index and its integration with Store.search."""

from datetime import datetime, timezone

import pytest

from scrollback import fts
from scrollback.models import Message, Part, Session
from scrollback.sources.base import Source
from scrollback.store import Store

pytestmark = pytest.mark.skipif(
    not fts.fts5_available(), reason="SQLite FTS5 not available in this build"
)


def _session(sid, title, *texts):
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    parts = tuple(Part(id=f"{sid}-p{i}", type="text", text=t) for i, t in enumerate(texts))
    msg = Message(id=f"{sid}-m1", role="assistant", created=created, parts=parts)
    return Session(id=sid, source="fake", title=title, directory="/tmp/proj",
                   created=created, updated=created, messages=(msg,), message_count=1)


class FakeSource(Source):
    name = "fake"
    label = "Fake"

    def __init__(self, sessions):
        self._sessions = {s.id: s for s in sessions}

    def is_available(self):
        return True

    def location(self):
        from pathlib import Path
        return Path("/tmp/fake")

    def list_sessions(self):
        return iter(self._sessions.values())

    def load_session(self, session_id):
        return self._sessions.get(session_id)


@pytest.fixture
def store():
    return Store([FakeSource([
        _session("s1", "First", "hello world about convergence"),
        _session("s2", "Second", "another conversation about pytest fixtures"),
    ])])


@pytest.fixture
def index(tmp_path):
    return fts.FtsIndex(tmp_path / "index.db")


def test_sync_builds_and_reports(index, store):
    stats = index.sync(store)
    assert stats["added"] == 2
    assert index.exists()
    s = index.stats()
    assert s["sessions"] == 2
    assert s["parts"] >= 2


def test_sync_is_incremental(index, store):
    index.sync(store)
    stats = index.sync(store)  # nothing changed
    assert stats["unchanged"] == 2
    assert stats["added"] == 0


def test_index_search_finds_term(index, store):
    index.sync(store)
    hits = list(index.search("convergence"))
    assert len(hits) == 1
    assert hits[0].session_id == "s1"


def test_index_search_multiword_is_and(index, store):
    index.sync(store)
    # both terms appear only in s2
    assert [h.session_id for h in index.search("pytest fixtures")] == ["s2"]
    # a term pair that doesn't co-occur yields nothing
    assert list(index.search("convergence pytest")) == []


def test_index_search_handles_operator_chars_safely(index, store):
    index.sync(store)
    # stray FTS operator characters must not raise
    assert isinstance(list(index.search("convergence*")), list)
    assert isinstance(list(index.search('"unbalanced')), list)


def test_store_uses_index_when_present(index, store, monkeypatch):
    index.sync(store)
    # Point the Store's FTS lookup at our temp index.
    monkeypatch.setattr(fts, "FtsIndex", lambda *a, **k: index)
    hits = list(store.search("convergence", use_index=True))
    assert len(hits) == 1
    assert hits[0].session.id == "s1"
    assert "convergence" in hits[0].snippet.lower()


def test_store_lexical_fallback_when_no_index(store):
    # No index file -> lexical scan still works.
    hits = list(store.search("pytest", use_index=True))
    assert [h.session.id for h in hits] == ["s2"]


def test_sync_prunes_deleted_sessions(index):
    s1 = _session("a", "A", "alpha text")
    s2 = _session("b", "B", "beta text")
    store2 = Store([FakeSource([s1, s2])])
    index.sync(store2)
    assert index.stats()["sessions"] == 2
    # Remove one session and re-sync.
    store1 = Store([FakeSource([s1])])
    stats = index.sync(store1)
    assert stats["removed"] == 1
    assert index.stats()["sessions"] == 1


def test_is_stale_detects_source_changes(tmp_path):
    # Use a real file-backed ClaudeCode source so mtimes are meaningful.
    import json
    import time

    from scrollback.sources.claudecode import ClaudeCodeSource

    proj = tmp_path / "projects" / "-p"
    proj.mkdir(parents=True)
    sid = "11111111-1111-1111-1111-111111111111"
    f = proj / f"{sid}.jsonl"
    f.write_text(json.dumps({
        "type": "user", "timestamp": "2026-06-01T10:00:00Z", "sessionId": sid,
        "cwd": "/p", "message": {"role": "user", "content": "hello"}}) + "\n")

    store = Store([ClaudeCodeSource(root=tmp_path / "projects")])
    index = fts.FtsIndex(tmp_path / "index.db")
    index.sync(store)
    assert index.is_stale(store) is False

    time.sleep(1.1)  # exceed the 1s slack
    with f.open("a") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": "2026-06-01T10:05:00Z", "sessionId": sid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "new"}]}}) + "\n")
    assert index.is_stale(store) is True
