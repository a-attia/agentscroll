"""Unified store: query across all (or selected) source adapters.

This is the single entry point the CLI and web app use. It composes the
individual adapters, applies cross-source filtering/sorting, and resolves
session selectors that may carry a `source:id` qualifier.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Message, Part, Session
from .sources import registry
from .sources.base import Source


def _sort_key(s: Session) -> datetime:
    return s.updated or s.created or datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single search match within a session."""

    session: Session
    message: Message
    part: Part
    snippet: str


class Store:
    """Facade over one or more source adapters."""

    def __init__(self, sources: list[Source] | None = None) -> None:
        self._sources = sources if sources is not None else registry.available_sources()

    @property
    def sources(self) -> list[Source]:
        return self._sources

    def with_sources(self, names: list[str]) -> "Store":
        chosen = [s for s in registry.all_sources() if s.name in names]
        return Store(chosen)

    # -- listing ------------------------------------------------------------

    def list_sessions(
        self,
        *,
        directory: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[Session]:
        """List sessions across sources, newest first.

        `directory`: keep only sessions whose directory contains this path
        substring. `query`: case-insensitive substring match on the title.
        """
        results: list[Session] = []
        for src in self._sources:
            for sess in src.list_sessions():
                if directory and (sess.directory is None or directory not in sess.directory):
                    continue
                if query and query.lower() not in (sess.title or "").lower():
                    continue
                results.append(sess)
        results.sort(key=_sort_key, reverse=True)
        if limit is not None:
            results = results[:limit]
        return results

    # -- single session -----------------------------------------------------

    def load_session(self, selector: str, *, source: str | None = None) -> Session | None:
        """Load one session by selector.

        Selector may be `source:id`, a full id, a unique prefix, or 'latest'.
        """
        src_name, sel = _split_selector(selector, source)
        candidates = self._sources if src_name is None else [
            s for s in self._sources if s.name == src_name
        ]
        # Try resolution within each candidate source.
        for src in candidates:
            full = src.resolve_session_id(sel)
            if full:
                return src.load_session(full)
        return None

    # -- search -------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        directory: str | None = None,
        limit: int | None = None,
        context: int = 80,
    ) -> Iterator[SearchHit]:
        """Yield hits where `query` (case-insensitive) appears in any part.

        Searches loaded message/part text across all listed sessions. This
        is a lexical scan (no index); fine for local single-user volumes
        and keeps the "no background process" promise.
        """
        ql = query.lower()
        count = 0
        for meta in self.list_sessions(directory=directory):
            src = next((s for s in self._sources if s.name == meta.source), None)
            if src is None:
                continue
            sess = src.load_session(meta.id)
            if sess is None:
                continue
            for msg in sess.messages:
                for part in msg.parts:
                    if not part.text:
                        continue
                    pos = part.text.lower().find(ql)
                    if pos == -1:
                        continue
                    yield SearchHit(
                        session=sess,
                        message=msg,
                        part=part,
                        snippet=_snippet(part.text, pos, len(query), context),
                    )
                    count += 1
                    if limit is not None and count >= limit:
                        return


def _split_selector(selector: str, source: str | None) -> tuple[str | None, str]:
    if source:
        return source, selector
    if ":" in selector:
        head, _, tail = selector.partition(":")
        if registry.get_source(head) is not None:
            return head, tail
    return None, selector


def _snippet(text: str, pos: int, qlen: int, context: int) -> str:
    start = max(0, pos - context)
    end = min(len(text), pos + qlen + context)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return (prefix + text[start:end] + suffix).replace("\n", " ")
