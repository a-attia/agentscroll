"""Live, read-only smoke tests against whatever data exists on this machine.

These tests skip gracefully when a source is not present, so CI without
local agent data still passes. They verify the adapters parse real data
and -- crucially -- that reading never mutates the source store.
"""

import os

import pytest

from agentscroll.sources.claudecode import ClaudeCodeSource
from agentscroll.sources.opencode import OpenCodeSource
from agentscroll.store import Store


def _mtime(path) -> float:
    return os.stat(path).st_mtime


# -- opencode --------------------------------------------------------------


def test_opencode_listing_and_load_are_readonly():
    src = OpenCodeSource()
    if not src.is_available():
        pytest.skip("opencode db not present")
    db = src.location()
    before = _mtime(db)

    sessions = list(src.list_sessions())
    assert sessions, "expected at least one opencode session"
    s0 = sessions[0]
    assert s0.source == "opencode"
    assert s0.id

    full = src.load_session(s0.id)
    assert full is not None
    assert full.id == s0.id
    # Messages should be ordered and carry roles.
    if full.messages:
        assert all(m.role for m in full.messages)

    after = _mtime(db)
    # Read-only invariant: the database file must not be modified by reads.
    assert before == after, "opencode db mtime changed during read-only access"


def test_opencode_resolve_latest():
    src = OpenCodeSource()
    if not src.is_available():
        pytest.skip("opencode db not present")
    full = src.resolve_session_id("latest")
    assert full is not None
    assert src.load_session(full) is not None


# -- claudecode ------------------------------------------------------------


def test_claudecode_listing_and_load():
    src = ClaudeCodeSource()
    if not src.is_available():
        pytest.skip("claude projects dir not present")
    sessions = list(src.list_sessions())
    if not sessions:
        pytest.skip("no claude sessions on disk")
    s0 = sessions[0]
    assert s0.source == "claudecode"
    full = src.load_session(s0.id)
    assert full is not None
    assert full.id == s0.id


# -- unified store ---------------------------------------------------------


def test_store_lists_and_sorts_newest_first():
    store = Store()
    if not store.sources:
        pytest.skip("no sources available")
    sessions = store.list_sessions(limit=20)
    assert sessions
    # Verify non-increasing recency order.
    keys = [(s.updated or s.created) for s in sessions if (s.updated or s.created)]
    assert keys == sorted(keys, reverse=True)


def test_store_search_finds_something_or_skips():
    store = Store()
    if not store.sources:
        pytest.skip("no sources available")
    # Search for a very common token; tolerate zero matches on tiny stores.
    hits = list(store.search("the", limit=3))
    for h in hits:
        assert "the" in h.snippet.lower() or h.part.text
