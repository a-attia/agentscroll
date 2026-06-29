"""The Source adapter contract.

A Source reads ONE agent's local, on-disk session store in read-only mode
and normalizes it into the common model (Session/Message/Part). All
methods must be side-effect free with respect to the agent's data: an
adapter must never write to, lock for writing, or otherwise mutate the
source store.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..models import Message, Session


class Source(abc.ABC):
    """Read-only adapter for a single AI-agent session store."""

    #: Stable machine name used in CLI flags and ids, e.g. "opencode".
    name: str = "base"
    #: Human-readable label for display, e.g. "opencode".
    label: str = "Base"

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if this agent's data store exists on this machine."""

    @abc.abstractmethod
    def location(self) -> Path | None:
        """Return the path this adapter reads from (for diagnostics)."""

    @abc.abstractmethod
    def list_sessions(self) -> Iterator[Session]:
        """Yield sessions with metadata only (no messages), newest concern aside.

        Ordering is not guaranteed here; callers sort as needed.
        """

    @abc.abstractmethod
    def load_session(self, session_id: str) -> Session | None:
        """Return a single session fully populated with messages, or None."""

    def load_session_meta(self, session_id: str) -> Session | None:
        """Return a single session's metadata only (no messages).

        Used by the web app to show a session header without paying the cost
        of loading every message. Default implementation loads everything and
        strips the messages; adapters should override for efficiency.
        """
        from dataclasses import replace

        sess = self.load_session(session_id)
        if sess is None:
            return None
        return replace(sess, messages=())

    def load_messages(
        self, session_id: str, *, offset: int = 0, limit: int | None = None
    ) -> list[Message]:
        """Return a slice of a session's messages (for windowed loading).

        Default implementation loads the whole session then slices; adapters
        should override to avoid loading everything for huge transcripts.
        """
        sess = self.load_session(session_id)
        if sess is None:
            return []
        msgs = list(sess.messages)
        if offset:
            msgs = msgs[offset:]
        if limit is not None:
            msgs = msgs[:limit]
        return msgs

    def resolve_session_id(self, selector: str) -> str | None:
        """Resolve a selector (full id, prefix, or 'latest') to a full id.

        Default implementation scans `list_sessions`. Adapters may override
        with a cheaper lookup.
        """
        selector = selector.strip()
        sessions = list(self.list_sessions())
        if not sessions:
            return None
        if selector == "latest":
            sessions.sort(
                key=lambda s: (s.updated or s.created or _MIN_DT()),
                reverse=True,
            )
            return sessions[0].id
        # Exact id first, then unique prefix.
        for s in sessions:
            if s.id == selector:
                return s.id
        matches = [s.id for s in sessions if s.id.startswith(selector)]
        if len(matches) == 1:
            return matches[0]
        return None

    def iter_messages(self, session_id: str) -> Iterable[Message]:
        """Convenience: yield the messages of one session."""
        sess = self.load_session(session_id)
        return sess.messages if sess else ()


def _MIN_DT():
    from datetime import datetime, timezone

    return datetime.min.replace(tzinfo=timezone.utc)
