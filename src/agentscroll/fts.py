"""Optional full-text search index (SQLite FTS5).

The default search path is a lexical scan over the live data (zero setup,
always correct, but O(corpus) per query). For large histories this builds
an opt-in inverted index in a *separate* cache database so queries are
near-instant. The source data stores are never touched for writing -- the
index is derived, disposable, and rebuilt from the read-only adapters.

Design
------
* Index DB lives at ``~/.cache/agentscroll/index.db`` (override with
  ``AGENTSCROLL_INDEX``). Deleting it just disables the fast path.
* ``parts`` is an FTS5 table holding each searchable part's text plus the
  metadata needed to reconstruct a hit (source, session id, message id,
  role, part type, tool name).
* ``synced`` records a per-session signature ``(updated_iso, message_count)``
  so :func:`sync` only re-indexes new/changed sessions and prunes deleted
  ones -- an incremental update, not a full rebuild.

Availability degrades gracefully: if FTS5 is missing, :func:`available`
returns False and callers fall back to the lexical scan.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


def default_index_path() -> Path:
    override = os.environ.get("AGENTSCROLL_INDEX")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "agentscroll" / "index.db"


def fts5_available() -> bool:
    """True if this Python's SQLite was built with FTS5."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
            return True
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return False


@dataclass(frozen=True, slots=True)
class IndexHit:
    """A raw FTS match -- enough to rebuild a SearchHit without re-scanning."""

    source: str
    session_id: str
    message_id: str
    role: str
    part_type: str
    tool_name: str | None
    text: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS synced (
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    updated TEXT,
    message_count INTEGER,
    PRIMARY KEY (source, session_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS parts USING fts5(
    source UNINDEXED,
    session_id UNINDEXED,
    message_id UNINDEXED,
    role UNINDEXED,
    part_type UNINDEXED,
    tool_name UNINDEXED,
    text,
    tokenize = 'unicode61'
);
"""


class FtsIndex:
    """Read/write wrapper around the cache index database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_index_path()

    # -- lifecycle ----------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
            conn.executescript(_SCHEMA)
        else:
            # Read-only open; raises if the file doesn't exist.
            uri = f"file:{self.path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    # -- sync ---------------------------------------------------------------

    def sync(self, store, *, progress=None) -> dict[str, int]:
        """Incrementally bring the index in line with `store`.

        Returns counts: {"added", "updated", "removed", "unchanged"}.
        `progress(done, total)` is called per session if provided.
        """
        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
        with self._connect(write=True) as conn:
            # Current signatures already in the index.
            have = {
                (r["source"], r["session_id"]): (r["updated"], r["message_count"])
                for r in conn.execute(
                    "SELECT source, session_id, updated, message_count FROM synced"
                )
            }
            # Live sessions (metadata only; cheap). Don't fold -- we want every
            # session, including subagents, individually indexed.
            live = store.list_sessions(fold_subagents=False)
            live_keys = {(s.source, s.id) for s in live}
            total = len(live)

            for i, meta in enumerate(live):
                key = (meta.source, meta.id)
                sig = (
                    meta.updated.isoformat() if meta.updated else None,
                    meta.message_count,
                )
                prev = have.get(key)
                if prev == sig:
                    stats["unchanged"] += 1
                else:
                    self._reindex_session(conn, store, meta, sig)
                    stats["added" if prev is None else "updated"] += 1
                if progress:
                    progress(i + 1, total)

            # Prune sessions that no longer exist on disk.
            for key in set(have) - live_keys:
                self._drop_session(conn, *key)
                stats["removed"] += 1
            conn.commit()
        return stats

    def _drop_session(self, conn: sqlite3.Connection, source: str, sid: str) -> None:
        conn.execute("DELETE FROM parts WHERE source = ? AND session_id = ?", (source, sid))
        conn.execute("DELETE FROM synced WHERE source = ? AND session_id = ?", (source, sid))

    def _reindex_session(self, conn, store, meta, sig) -> None:
        self._drop_session(conn, meta.source, meta.id)
        # Pass source explicitly (not "source:id"): the selector form only
        # recognizes source prefixes registered in the global registry.
        sess = store.load_session(meta.id, source=meta.source)
        if sess is not None:
            rows = []
            for m in sess.messages:
                for p in m.parts:
                    if p.text:
                        rows.append(
                            (meta.source, meta.id, m.id, m.role, p.type,
                             p.tool_name, p.text)
                        )
            if rows:
                conn.executemany(
                    "INSERT INTO parts (source, session_id, message_id, role, "
                    "part_type, tool_name, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        conn.execute(
            "INSERT OR REPLACE INTO synced (source, session_id, updated, message_count) "
            "VALUES (?, ?, ?, ?)",
            (meta.source, meta.id, sig[0], sig[1]),
        )

    # -- query --------------------------------------------------------------

    def search(self, query: str, *, limit: int | None = None,
               sources: list[str] | None = None) -> Iterator[IndexHit]:
        """Yield IndexHits for an FTS query (newest indexing order)."""
        if not self.exists() or not query.strip():
            return iter(())
        return self._search(query, limit, sources)

    def _search(self, query, limit, sources) -> Iterator[IndexHit]:
        match = _to_match_query(query)
        sql = (
            "SELECT source, session_id, message_id, role, part_type, tool_name, "
            "  snippet(parts, 6, '\x02', '\x03', '…', 16) AS snip "
            "FROM parts WHERE parts MATCH ?"
        )
        params: list[object] = [match]
        if sources:
            placeholders = ",".join("?" * len(sources))
            sql += f" AND source IN ({placeholders})"
            params.extend(sources)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect(write=False) as conn:
            for r in conn.execute(sql, params):
                yield IndexHit(
                    source=r["source"],
                    session_id=r["session_id"],
                    message_id=r["message_id"],
                    role=r["role"],
                    part_type=r["part_type"],
                    tool_name=r["tool_name"],
                    text=r["snip"],
                )

    def stats(self) -> dict[str, int]:
        if not self.exists():
            return {"sessions": 0, "parts": 0}
        with self._connect(write=False) as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM synced").fetchone()[0]
            parts = conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
        return {"sessions": sessions, "parts": parts}


def _to_match_query(query: str) -> str:
    """Turn a user query into a safe FTS5 MATCH expression.

    We quote each whitespace-separated term as a phrase (doubling embedded
    quotes) and AND them together. This avoids FTS5 operator-syntax errors
    from arbitrary user input (e.g. a stray `*` or `:`), while still giving
    multi-word AND semantics.
    """
    terms = query.split()
    if not terms:
        return '""'
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)
