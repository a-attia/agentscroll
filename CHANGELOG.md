# Changelog

All notable changes to scrollback are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and the project aims to
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-06-30

### Added

- **Usage statistics page (web) + per-tool breakdown.** A new `stats` view
  aggregates usage by tool (opencode / Claude Code / Codex / Aider) plus an
  overall total: sessions, messages, input/output/cache tokens, and cost
  (shown only where the tool records it). `scrollback stats` gains the same
  per-tool table and a `--json` breakdown; a `/api/stats` endpoint backs the
  web page. Both honour `since`/`until` date filters (new `--since`/`--until`
  on the CLI `stats` command).
- **Redesigned top bar.** A radio-style `browse | stats` view switch (exactly
  one active) replaces the buried icon buttons; the brand mark resets to home;
  the theme toggle is pinned to the far right as a setting.
- **Responsive session drawer.** On narrow screens (e.g. split-screen) the
  session list becomes a slide-in drawer opened by a `sessions` button, with
  a backdrop; it closes on selection, backdrop click, or `Esc`. Previously the
  list was hidden with no way to reach it.
- The empty state now shows the full app icon.

### Fixed

- A genuine reported cost of `$0.00` is now kept distinct from "cost unknown"
  in the per-tool usage rollup (was collapsed to unknown by a truthiness check).

## [0.2.0] - 2026-06-30

### Added

- **Cache & reasoning token accounting.** Sessions now carry
  `tokens_cache_read`, `tokens_cache_write`, and `tokens_reasoning` in
  addition to input/output/cost. In agentic sessions cache reads often
  dominate total token volume, so this makes scrollback's usage numbers
  reconcilable with the agents' own reports:
  - **opencode** reads the corresponding SQLite columns (tolerant of older
    databases that lack them).
  - **Claude Code** now reports usage at all — summed per-turn from each
    assistant message's `usage` block (previously blank).
  - **Codex** parses token-count records where the rollout format includes
    them (best-effort; `None` when absent).
  - **Aider** has no token data on disk and stays `None`.
- `stats` shows a `cache` (read/write) line and a `reasoning` line; the web
  transcript header shows a cache figure; Markdown/HTML/JSON exports include
  a usage summary.

## [0.1.2] - 2026-06-30

### Added

- `scrollback uninstall`: removes the artifacts scrollback created (Desktop
  launcher, macOS `.app`, optional search index, launcher log) with a
  confirmation prompt (`--yes` / `--dry-run`). It never touches agent data
  and never self-removes the package; it prints the right `pip`/`pipx
  uninstall` command instead.

## [0.1.1] - 2026-06-30

### Fixed

- README images now render on PyPI: use absolute, release-pinned PNG URLs
  (PyPI does not resolve relative paths or display SVGs). Adds a PyPI-
  friendly `cli.png` alongside the GitHub SVG.

## [0.1.0] - 2026-06-30

The first release. scrollback reads AI coding-agent session history
(opencode + Claude Code) read-only and lets you browse, search, copy, and
export it from a CLI and a local web app.

### Added

- **CLI** (`scrollback`): `sources`, `list`, `show`, `search`, `export`
  (markdown / json / html / text), `copy`, `stats`, `resume`, `web`,
  `index`, `doctor`, and `install-launcher`.
- **Source adapters** (pluggable, read-only): opencode (SQLite), Claude Code
  (JSONL, with subagent sidechains folded under their parent), Codex
  (`rollout-*.jsonl`), and Aider (`.aider.chat.history.md`). More are queued
  in `ROADMAP.md`.
- `stats` aggregates session/message/token/cost totals plus top projects;
  `resume` prints the native command to continue a session in its own agent.
- Listing filters: `--source`, `--dir`, `--query`, `--since` / `--until`,
  pagination (`--offset` / `--page`), usage columns (`--usage`), and
  subagent folding (on by default; `--no-fold`). Optional coloured output
  via `rich`.
- **Web app** (`scrollback web`): local, read-only, served on
  `127.0.0.1`. Session list with source filters, date filters, and a
  `titles | contents` search scope; lazy, windowed transcript loading so
  very large sessions open instantly; in-transcript find; per-message and
  per-session copy; export and print; light/dark theme; keyboard
  navigation; a frozen session header with a scrolling message body.
- **Markdown rendering**: assistant/user text renders as Markdown with code
  highlighting -- in the browser (vendored marked + highlight.js) and in
  the static HTML export (a dependency-free Python renderer + highlighter).
- **Math / equation rendering**: delimited LaTeX (`$...$`, `$$...$$`,
  `\(...\)`, `\[...\]`) is detected and shielded from the Markdown pass so
  `\`, `_`, `*`, `^` survive intact in both renderers. A render mode --
  `raw` (verbatim source), `latex` (verbatim, never typeset, paste-ready),
  or `rendered` (typeset) -- is a toggle in the web transcript header
  (persisted like the theme) and an `--math {raw,latex,rendered}` flag on
  `scrollback export` / `copy`. Typesetting uses vendored KaTeX (no CDN);
  the self-contained HTML export embeds KaTeX with its fonts inlined so
  saved/printed files typeset offline. The single-`$` form is recognised
  conservatively so currency (`$5 to $10`) and code are left alone.
- **Optional full-text search index** (`scrollback index`): SQLite FTS5,
  incremental, stored in a disposable cache DB; the source data is never
  modified, and search falls back to a lexical scan without it.
- **Launching without the terminal**: `scrollback-web` / `scrollback-app`
  console entry points; `install-launcher` drops a double-clickable
  launcher (macOS `.command` / `.app`, Windows `.bat`, Linux `.desktop`);
  a native desktop window via pywebview that frees the port on close.
- App icon (macOS `.app` + web favicon) and macOS app identity (menu name,
  About panel with version and a clickable repo link).
- Configurable host/port via flags or `SCROLLBACK_HOST` / `SCROLLBACK_PORT`,
  with automatic free-port selection.

### Security

- Sanitize rendered Markdown (DOMPurify) to prevent transcript content from
  injecting scripts into the web UI.
- Host-header allowlist guarding against DNS-rebinding (loopback-only by
  default); loud warning on non-loopback binds.
- Path-traversal containment for Claude subagent id resolution.

### Performance

- Cache Claude Code metadata scans by file mtime (repeated listings go from
  ~1.2s to ~0.01s).
- Byte-offset paging index for Claude transcripts (deep pages on a
  31k-message session: ~1s to ~2ms).
- Lazy per-session metadata resolution on the indexed search path.

### Fixed

- Timezone-naive timestamps no longer crash session sorting.
- Subagent folding no longer drops self-referential or cross-source records.
- Reliable downloads and printing from the native desktop window.
- Negative pagination arguments are rejected; clearer errors for unknown
  sources, failed exports, and unavailable data sources.

[Unreleased]: https://github.com/a-attia/scrollback/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/a-attia/scrollback/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/a-attia/scrollback/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/a-attia/scrollback/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/a-attia/scrollback/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/a-attia/scrollback/releases/tag/v0.1.0
