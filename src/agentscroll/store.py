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
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        fold_subagents: bool = False,
    ) -> list[Session]:
        """List sessions across sources, newest first.

        Args:
          directory: keep only sessions whose directory contains this substring.
          query: case-insensitive substring match on the title.
          since / until: keep sessions whose updated (or created) time falls
            within the range (inclusive).
          limit / offset: pagination over the filtered, sorted result.
          fold_subagents: nest subagent sessions under their parent (as
            `.children`) instead of listing them at the top level.
        """
        results: list[Session] = []
        for src in self._sources:
            for sess in src.list_sessions():
                if directory and (sess.directory is None or directory not in sess.directory):
                    continue
                if query and query.lower() not in (sess.title or "").lower():
                    continue
                when = sess.updated or sess.created
                if since and (when is None or when < since):
                    continue
                if until and (when is None or when > until):
                    continue
                results.append(sess)
        results.sort(key=_sort_key, reverse=True)

        if fold_subagents:
            results = _fold(results)

        if offset:
            results = results[offset:]
        if limit is not None:
            results = results[:limit]
        return results

    # -- single session -----------------------------------------------------

    def _resolve(self, selector: str, source: str | None):
        """Return (Source, full_id) for a selector, or (None, None)."""
        src_name, sel = _split_selector(selector, source)
        candidates = self._sources if src_name is None else [
            s for s in self._sources if s.name == src_name
        ]
        for src in candidates:
            full = src.resolve_session_id(sel)
            if full:
                return src, full
        return None, None

    def load_session_meta(self, selector: str, *, source: str | None = None) -> Session | None:
        """Load only a session's metadata (no messages) -- cheap for huge ones."""
        src, full = self._resolve(selector, source)
        return src.load_session_meta(full) if src else None

    def load_messages(
        self, selector: str, *, source: str | None = None,
        offset: int = 0, limit: int | None = None,
    ) -> list[Message]:
        """Load a windowed slice of a session's messages."""
        src, full = self._resolve(selector, source)
        return src.load_messages(full, offset=offset, limit=limit) if src else []

    def load_session(self, selector: str, *, source: str | None = None) -> Session | None:
        """Load one session (with all messages) by selector.

        Selector may be `source:id`, a full id, a unique prefix, or 'latest'.
        """
        src, full = self._resolve(selector, source)
        return src.load_session(full) if src else None

    # -- search -------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        directory: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        context: int = 80,
        use_index: bool = True,
    ) -> Iterator[SearchHit]:
        """Yield hits where `query` appears in any message part.

        If an FTS index exists (built via `agentscroll index`) and
        `use_index` is True, the fast indexed path is used. Otherwise this
        falls back to a lexical scan over the live data -- zero setup, always
        correct, but O(corpus) per query.
        """
        if use_index:
            indexed = self._search_indexed(
                query, directory=directory, since=since, until=until, limit=limit
            )
            if indexed is not None:
                yield from indexed
                return
        yield from self._search_lexical(
            query, directory=directory, since=since, until=until,
            limit=limit, context=context,
        )

    def _search_indexed(
        self, query, *, directory, since, until, limit
    ) -> Iterator[SearchHit] | None:
        """Indexed search. Returns None if the index is unavailable (caller
        then falls back to lexical).

        The FTS query itself is ~instant; the only expensive thing is mapping
        a hit's session id back to session metadata. So we resolve metadata
        *lazily* per distinct session that actually appears in results
        (usually few), via the cheap per-adapter `load_session_meta`. We only
        pay for a full `list_sessions` when directory/date filters are given.
        """
        from . import fts

        index = fts.FtsIndex()
        if not index.exists():
            return None

        source_names = [s.name for s in self._sources]
        filtering = directory is not None or since is not None or until is not None
        allowed: set[tuple[str, str]] | None = None
        if filtering:
            allowed = {
                (s.source, s.id)
                for s in self.list_sessions(directory=directory, since=since, until=until)
            }

        meta_cache: dict[tuple[str, str], Session | None] = {}

        def meta_for(source: str, sid: str) -> Session | None:
            k = (source, sid)
            if k not in meta_cache:
                src = next((s for s in self._sources if s.name == source), None)
                meta_cache[k] = src.load_session_meta(sid) if src else None
            return meta_cache[k]

        def gen() -> Iterator[SearchHit]:
            count = 0
            for hit in index.search(query, sources=source_names):
                key = (hit.source, hit.session_id)
                if allowed is not None and key not in allowed:
                    continue
                meta = meta_for(hit.source, hit.session_id)
                if meta is None:
                    continue  # stale index entry (session deleted)
                part = Part(id="", type=hit.part_type, text="", tool_name=hit.tool_name)
                msg = Message(id=hit.message_id, role=hit.role, created=None, parts=(part,))
                yield SearchHit(
                    session=meta,
                    message=msg,
                    part=part,
                    snippet=_clean_snippet(hit.text),
                )
                count += 1
                if limit is not None and count >= limit:
                    return

        return gen()

    def _search_lexical(
        self, query, *, directory, since, until, limit, context
    ) -> Iterator[SearchHit]:
        ql = query.lower()
        count = 0
        for meta in self.list_sessions(directory=directory, since=since, until=until):
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


def _fold(sessions: list[Session]) -> list[Session]:
    """Nest subagent sessions under their parent.

    A session with a `parent_id` that matches another session's id becomes a
    child of that parent (attached via `.children`). Subagents whose parent
    is not in the list stay at top level so nothing is lost. Order among
    top-level sessions is preserved; children keep newest-first order.
    """
    from dataclasses import replace

    # Key on (source, id): ids are only unique within a source, so keying on
    # the bare id could mis-link a parent in one source to a child in another.
    def key(source: str, sid: str) -> tuple[str, str]:
        return (source, sid)

    by_key = {key(s.source, s.id): s for s in sessions}
    children_of: dict[tuple[str, str], list[Session]] = {}
    top: list[Session] = []
    for s in sessions:
        parent_key = key(s.source, s.parent_id) if s.parent_id else None
        # Fold only when the parent exists AND isn't the session itself
        # (a self-referential parent_id would otherwise drop the session).
        if parent_key and parent_key != key(s.source, s.id) and parent_key in by_key:
            children_of.setdefault(parent_key, []).append(s)
        else:
            top.append(s)
    return [
        replace(s, children=tuple(children_of.get(key(s.source, s.id), ())))
        if key(s.source, s.id) in children_of
        else s
        for s in top
    ]


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


def _clean_snippet(snip: str) -> str:
    """Normalize an FTS5 snippet for display.

    The index requests snippets with \\x02/\\x03 wrapping the matched term
    (so the frontend/CLI can re-highlight without re-searching). We strip the
    markers here and collapse newlines; the consumers do their own
    highlighting against the query, matching the lexical path's snippets.
    """
    return snip.replace("\x02", "").replace("\x03", "").replace("\n", " ").strip()
