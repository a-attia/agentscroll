"""Tests for the stats aggregation and resume-command generation."""

from datetime import datetime, timezone

from scrollback.models import Session
from scrollback.sources.base import Source
from scrollback.store import Store


def _mk(sid, source, day, *, directory, messages, ti=0, to=0, cost=0.0):
    dt = datetime(2026, 6, day, tzinfo=timezone.utc)
    return Session(id=sid, source=source, title=f"s{sid}", directory=directory,
                   created=dt, updated=dt, message_count=messages,
                   tokens_input=ti, tokens_output=to, cost=cost)


class FakeSource(Source):
    def __init__(self, name, sessions):
        self._name = name
        self._sessions = {s.id: s for s in sessions}

    @property
    def name(self):
        return self._name

    def is_available(self):
        return True

    def location(self):
        from pathlib import Path
        return Path("/tmp/fake")

    def list_sessions(self):
        return iter(self._sessions.values())

    def load_session(self, sid):
        return self._sessions.get(sid)

    def resume_command(self, session):
        return f"{self._name} --resume {session.id}"


def _store():
    a = FakeSource("alpha", [
        _mk("1", "alpha", 1, directory="/proj/foo", messages=10, ti=100, to=20, cost=0.5),
        _mk("2", "alpha", 5, directory="/proj/foo", messages=5, ti=50, to=10),
    ])
    b = FakeSource("beta", [
        _mk("3", "beta", 3, directory="/proj/bar", messages=7, ti=70, to=7, cost=0.25),
    ])
    return Store([a, b])


def test_stats_aggregates():
    st = _store().stats()
    assert st.sessions == 3
    assert st.per_source == {"alpha": 2, "beta": 1}
    assert st.per_project == {"/proj/foo": 2, "/proj/bar": 1}
    assert st.total_messages == 22
    assert st.total_tokens_input == 220
    assert st.total_tokens_output == 37
    assert abs(st.total_cost - 0.75) < 1e-9
    assert st.oldest.day == 1 and st.newest.day == 5


def test_stats_empty_store():
    st = Store([]).stats()
    assert st.sessions == 0
    assert st.per_source == {}
    assert st.oldest is None and st.newest is None


def test_resume_command_per_source():
    store = _store()
    src, full = store._resolve("1", "alpha")
    sess = src.load_session_meta(full)
    assert src.resume_command(sess) == "alpha --resume 1"


def test_resume_command_none_falls_through():
    # A source without a by-id resume returns None (base default).
    class NoResume(FakeSource):
        def resume_command(self, session):
            return None

    s = NoResume("gamma", [_mk("9", "gamma", 2, directory="/p", messages=1)])
    store = Store([s])
    src, full = store._resolve("9", "gamma")
    assert src.resume_command(src.load_session_meta(full)) is None
