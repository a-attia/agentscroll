"""Tests for store-level filtering, pagination, and subagent folding.

Uses a synthetic in-memory source so results are deterministic.
"""

from datetime import datetime, timezone

from scrollback.models import Session
from scrollback.sources.base import Source
from scrollback.store import Store


def _mk(sid, day, *, parent=None):
    dt = datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc)
    return Session(
        id=sid,
        source="fake",
        title=f"session {sid}",
        directory="/tmp/proj",
        created=dt,
        updated=dt,
        parent_id=parent,
        message_count=1,
    )


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


def _store():
    sessions = [
        _mk("a", 10),
        _mk("b", 20),
        _mk("c", 25, parent="b"),   # subagent of b
        _mk("d", 28),
    ]
    return Store([FakeSource(sessions)])


def test_since_until_filter():
    store = _store()
    # since 2026-06-21 should drop a (10) and b (20); keep c (25), d (28).
    res = store.list_sessions(since=datetime(2026, 6, 21, tzinfo=timezone.utc))
    ids = {s.id for s in res}
    assert ids == {"c", "d"}

    res = store.list_sessions(until=datetime(2026, 6, 21, tzinfo=timezone.utc))
    ids = {s.id for s in res}
    assert ids == {"a", "b"}


def test_offset_and_limit():
    store = _store()
    allrows = store.list_sessions()
    assert [s.id for s in allrows] == ["d", "c", "b", "a"]  # newest first
    page2 = store.list_sessions(limit=2, offset=2)
    assert [s.id for s in page2] == ["b", "a"]


def test_fold_subagents_nests_child_under_parent():
    store = _store()
    folded = store.list_sessions(fold_subagents=True)
    ids = [s.id for s in folded]
    # c is folded under b, so top-level is d, b, a (c removed from top).
    assert ids == ["d", "b", "a"]
    b = next(s for s in folded if s.id == "b")
    assert [c.id for c in b.children] == ["c"]
    assert b.children[0].is_subagent


def test_fold_keeps_orphan_subagent_at_top():
    # A subagent whose parent is absent must not vanish.
    sessions = [_mk("x", 5, parent="missing")]
    store = Store([FakeSource(sessions)])
    folded = store.list_sessions(fold_subagents=True)
    assert [s.id for s in folded] == ["x"]


def test_fold_self_referential_parent_is_not_dropped():
    # Regression: a session whose parent_id == its own id must stay top-level,
    # not silently disappear.
    sessions = [_mk("self", 5, parent="self")]
    store = Store([FakeSource(sessions)])
    folded = store.list_sessions(fold_subagents=True)
    assert [s.id for s in folded] == ["self"]
    assert folded[0].children == ()
