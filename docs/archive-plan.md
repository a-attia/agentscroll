# Plan: Durable Session Archive ("keep sessions forever")

Status: **planned** (not started). This is the design of record for the
bulk save / archive feature. Written to be picked up in a dedicated
implementation session. Nothing here is built yet.

See also: [`ROADMAP.md`](../ROADMAP.md) (index of planned work),
[`CONTRIBUTING.md`](../CONTRIBUTING.md) (the read-only invariant and
adapter conventions).

---

## 1. Goal & scope

A one-way, durable **local vault**: scrollback copies the sessions it reads
into a user-owned archive and keeps them **forever**, surviving the agents'
own auto-deletion (e.g. Claude Code's ~30-day `cleanupPeriodDays`). The
archive is **lossless** and **re-readable** as a first-class source, so
browse / search / export / stats work over archived sessions — including
ones the agent has already deleted.

**In scope (v1):** local durable vault, incremental one-way sync
(agents -> vault), lossless format, read-back as a source.

**Out of scope (v1):** multi-machine sync, cloud/remote push. A user may
point the vault at a synced folder (Dropbox / iCloud / git) themselves.

### Decisions locked in (from the planning discussion)

- **Extend scrollback; do not fork.** The feature is built entirely on the
  existing engine (`Source` adapters, `Store`, the
  `list_sessions(fold_subagents=False)` drive loop, `(source, id)` keying,
  the incremental-sync pattern proven in `fts.py`). A separate tool would
  duplicate or depend on all of that. Framing: scrollback **reads** your
  agents (read-only, always) and can **archive** what it reads to your own
  vault — two verbs, one clear boundary.
- **Sync scope:** durable local vault, one-way.
- **Format:** lossless normalized (keeps `raw`), re-readable as a source.
- **Read-back:** yes — the archive is a first-class readable source.
- **Vault home:** dot-namespaced `~/.scrollback/` for durable, user-owned
  scrollback state (archive + future config), distinct from the disposable
  `~/.cache/scrollback/`.

---

## 2. Guiding principles

1. **The read-only invariant is untouched.** scrollback writes only to its
   own vault, never back to source stores. Add tests asserting source files
   are unmodified after a sync (mirroring
   `tests/test_sources_live.py:24-46`).
2. **Reuse the proven `FtsIndex.sync` pattern** (`fts.py:116-163`) —
   signature-based incremental sync keyed on `(source, id)` — **with the
   prune step inverted**: the archive never deletes a session that vanished
   from the source (that is the entire point).
3. **Everything keys on `(source, id)`.** Session ids are unique only within
   a source, not globally (`store.py:405-408`, `fts.py:76`).
4. **Three storage tiers, cleanly separated:**
   - `~/.cache/scrollback/` — **disposable** (FTS index, browser profile).
     Removed by `scrollback uninstall`.
   - `~/.scrollback/` — **durable, user-owned** (the vault + future config).
     **Survives `uninstall`** unless explicitly purged.
   - Source stores (`~/.claude/...`, the opencode DB, ...) — **read-only,
     never touched.**

---

## 3. Storage layout (`~/.scrollback/`)

```text
~/.scrollback/
├── config.json                 # future scrollback settings (archive path, ...)
└── archive/                    # the vault (override: --dest / SCROLLBACK_ARCHIVE / config)
    ├── manifest.sqlite         # index: (source,id) -> signature, path, first/last-seen
    └── sessions/
        └── <source>/<id>.json  # one lossless JSON per session (shard if needed)
```

- **Path resolution order:** `--dest` flag -> `SCROLLBACK_ARCHIVE` env ->
  `~/.scrollback/config.json` -> default `~/.scrollback/archive`.
- Filenames must be sanitized: Claude Code subagent ids contain `::`
  (`claudecode.py:30`); opencode ids are safe. Shard by a hash prefix if a
  `<source>/` directory would grow too large.

---

## 4. Components

### Component 1 — Lossless serialize / deserialize (`models.py` + new `archivefmt.py`)

The current JSON export is **lossy** — it strips every `raw` blob
(`export.py:137-144`) — and there is **no deserializer** anywhere in the
codebase. The archive needs both directions, losslessly.

- `archivefmt.to_archive_json(session)`: full `asdict(session)` **keeping**
  all `raw` blobs, wrapped in a small envelope (`schema_version`,
  `archived_at`, `scrollback_version`). Must be JSON-native: datetimes
  serialized as explicit ISO strings (not via the lossy `str()` fallback in
  `export.to_json`'s `default=`). Each adapter's `raw` is already parsed
  JSON / JSON-safe objects — verify during implementation.
- `Session.from_dict` / `Message.from_dict` / `Part.from_dict` (new, in
  `models.py`): reconstruct the frozen dataclasses from the archive dict,
  including `raw`, `children`, `messages`, and datetime parsing. This is the
  missing round-trip half.
- `schema_version` lets a future scrollback migrate old archives.

**Linchpin test:** `from_dict(to_archive_json(s)) == s` for real sessions
from every adapter.

### Component 2 — Archive store + incremental sync (`archive.py`)

Modeled on `FtsIndex` (`fts.py`):

- `ArchiveStore(path)` owns `manifest.sqlite` with a table like
  `archived(source, session_id, updated, message_count, first_archived,
  last_synced, file_path, PRIMARY KEY(source, session_id))`.
- `sync(store, *, sources=None, progress=None) -> {"added", "updated",
  "unchanged", "kept_orphan"}`:
  1. Enumerate live via `store.list_sessions(fold_subagents=False)` (the
     `fts.py:133` pattern — every session incl. subagents).
  2. Signature `(updated.isoformat(), message_count)` (`fts.py:139-142`).
     Unchanged -> skip; else `load_session(id, source=...)` fully and write
     the lossless JSON + upsert the manifest.
  3. **No prune.** Sessions in the manifest but absent from live are
     **kept** (counted `kept_orphan`) — the durability guarantee. Optionally
     record `last_seen_live` so we can report "N archived sessions no longer
     exist in their agent."
- Incrementality keeps re-syncs cheap; only new/changed sessions are
  re-serialized.

### Component 3 — Archive as a readable source (`sources/archive.py`, `ArchiveSource`)

- Reads `sessions/<source>/<id>.json` back via `from_dict`, exposing them
  through the normal `Source` interface, so browse / search / export / stats
  work over archived sessions **including ones the agent deleted**.
- **Preserves the original `(source, id)`**: a session archived from
  opencode still reports `source="opencode"`, so dedup on `(source, id)`
  works and provenance is kept.
- **Registration problem to solve:** `registry.py:27` instantiates adapters
  with `cls()` (no args); `ArchiveSource` needs a vault path and must be
  inactive when no vault exists. Plan: do **not** add it to `ALL_SOURCES`;
  instead have `Store` inject an archive source when a vault exists (e.g.
  `Store.with_archive(path)` or a constructor flag). It must not appear as a
  "known but unavailable" chip when no vault exists.
- **Loop-safety:** archive sync reads *live* sources only, never the
  `ArchiveSource`, so it can never archive its own archive.

### Component 4 — CLI `archive` command (`cli.py`)

Model on `cmd_index` (`cli.py:316`); slot near it (`cli.py:1122`).

- `scrollback archive` — incremental sync (the main verb); `--source`,
  `--dest`, `--since/--until`, progress output.
- `scrollback archive --stats` — vault size, per-source counts, how many
  archived sessions no longer exist live.
- Integrate with `list` (e.g. `list --source archive`) once Component 3
  lands.
- Later: `scrollback archive --verify` (integrity), `--export <dir>` (bulk
  export from the vault).
- Wire into `cmd_doctor` (`cli.py:151`) — show vault path + count. Wire into
  `cmd_uninstall` (`cli.py:947`) — **offer** to remove the vault but
  **default to keeping it** (durable user data, unlike the cache/index that
  `uninstall` deletes today at `cli.py:958`).

### Component 5 — Web integration (later phase)

A "sync now" affordance, archived-session badges, and an archive filter chip
in the web UI. Deferred to keep v1 focused on the CLI engine.

### Component 6 — Config file (`~/.scrollback/config.json`)

Minimal for now: the archive path override + a schema version. Establishes
the durable-config home for future scrollback behavior. Read during path
resolution; written by a future `scrollback config` command (not v1).

---

## 5. Testing strategy

- **Round-trip fidelity** (Component 1): `from_dict(to_archive_json(s)) == s`
  per adapter, using synthetic fixtures / the demo-data builders.
- **Incremental sync**: synthetic store — first sync archives all; second
  sync with one changed `message_count` re-archives only that one; a session
  removed from the live store stays in the vault (`kept_orphan`).
- **Read-back**: `ArchiveSource` over a temp vault returns sessions equal to
  the originals; a session deleted from its source is still readable.
- **Read-only invariant**: source mtimes unchanged after a sync (parallels
  `test_sources_live.py:46`).
- **Dedup**: a live+archived `(source, id)` appears once, with the chosen
  precedence.
- All using `tmp_path`; no real user data.

---

## 6. Phasing (each phase independently releasable)

- **Phase 1 — Lossless core:** `Session.from_dict` + `archivefmt` +
  round-trip tests. Foundation; no user-facing change.
- **Phase 2 — Archive engine + CLI:** `ArchiveStore.sync`,
  `scrollback archive`, doctor/uninstall wiring, tests. Ships the durable
  local vault.
- **Phase 3 — Read-back:** `ArchiveSource` + Store injection + dedup +
  tests. Archived sessions become browsable / searchable / exportable,
  including deleted ones.
- **Phase 4 — Web + polish:** UI sync/badges; `--verify`; opt-in
  auto-sync-on-web-launch (like `_background_index_refresh`, `cli.py:713`).
- **Phase 5 (future, out of v1):** multi-machine / cloud sync.

---

## 7. Open questions to resolve at the start of the implementation session

1. **Auto-sync vs. manual (v1).** Manual only (`scrollback archive`), or
   also opportunistic auto-sync when running `web`/`list` (like the FTS
   background refresh)? Leaning: **manual in v1**, opt-in auto later.
2. **Dedup precedence** when a session is both live and archived. Leaning:
   **live wins** (fresher) + an **"archived" badge** on sessions that exist
   only in the vault (deleted from the agent).
3. **Versioning on change.** When an archived session later grows, do we
   **overwrite** the archived copy (latest canonical) or keep **historical
   versions**? Leaning: **overwrite** — a session only ever appends/grows;
   versioning adds much complexity for little value.
4. **opencode fidelity.** Its `Session.raw` is empty (`{}`) and it is a
   shared SQLite DB, so there is no per-session file to byte-copy. Lossless
   archiving relies on the `messages`/`parts` (and their `raw`) we load.
   Confirm this normalized-only fidelity is acceptable for opencode.
5. **Uninstall default.** Keep the vault on `scrollback uninstall` unless a
   `--purge-archive` flag is given. Confirm.

---

## 8. Key code references (grounding for implementation)

- Incremental sync pattern to mirror: `fts.py:116-163` (`FtsIndex.sync`),
  staleness `fts.py:165-187`.
- Session id non-uniqueness / `(source,id)` keying: `store.py:405-408`,
  `fts.py:66-88`.
- Lossy JSON export (to invert for the archive): `export.py:131-144`.
- Where `raw` / on-disk paths live: `models.py:62-128`; adapters set
  `Session.raw["path"]` for JSONL/markdown
  (`claudecode.py:230`, `codex.py:120`, `aider.py:243`); opencode leaves
  `Session.raw` empty.
- Source contract: `sources/base.py:19-111`.
- Registry (instantiates `cls()` with no args): `sources/registry.py:17-32`.
- CLI subcommand registration + `cmd_index` template: `cli.py:316`,
  `cli.py:1122`; uninstall artifact handling `cli.py:947-996`.
- Read-only test to parallel: `tests/test_sources_live.py:24-46`.
- Durable-vs-disposable path precedent: `fts.default_index_path()`
  (`fts.py:33-37`), `~/.cache/scrollback/` in `webopen.py:78`.
