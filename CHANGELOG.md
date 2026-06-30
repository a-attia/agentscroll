# Changelog

All notable changes to agentscroll are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and the project aims to
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

The first development cycle toward 0.1.0. agentscroll reads AI coding-agent
session history (opencode + Claude Code) read-only and lets you browse,
search, copy, and export it from a CLI and a local web app.

### Added

- **CLI** (`agentscroll`): `sources`, `list`, `show`, `search`, `export`
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
- **Web app** (`agentscroll web`): local, read-only, served on
  `127.0.0.1`. Session list with source filters, date filters, and a
  `titles | contents` search scope; lazy, windowed transcript loading so
  very large sessions open instantly; in-transcript find; per-message and
  per-session copy; export and print; light/dark theme; keyboard
  navigation; a frozen session header with a scrolling message body.
- **Markdown rendering**: assistant/user text renders as Markdown with code
  highlighting -- in the browser (vendored marked + highlight.js) and in
  the static HTML export (a dependency-free Python renderer + highlighter).
- **Optional full-text search index** (`agentscroll index`): SQLite FTS5,
  incremental, stored in a disposable cache DB; the source data is never
  modified, and search falls back to a lexical scan without it.
- **Launching without the terminal**: `agentscroll-web` / `agentscroll-app`
  console entry points; `install-launcher` drops a double-clickable
  launcher (macOS `.command` / `.app`, Windows `.bat`, Linux `.desktop`);
  a native desktop window via pywebview that frees the port on close.
- App icon (macOS `.app` + web favicon) and macOS app identity (menu name,
  About panel with version and a clickable repo link).
- Configurable host/port via flags or `AGENTSCROLL_HOST` / `AGENTSCROLL_PORT`,
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
