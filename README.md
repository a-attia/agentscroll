# agentscroll

Navigate, search, copy, and export your AI coding-agent sessions from one
local, read-only tool.

`agentscroll` reads the session history that AI coding agents already keep
on disk — **opencode** (SQLite) and **Claude Code** (JSONL) today, more via
pluggable adapters — and lets you list, view, search, export, and copy
those conversations from the command line. Everything is local-first and
strictly **read-only**: it never modifies, locks for writing, or uploads
your data.

## Why

AI agents persist rich session data locally, but each in its own format
and with no good way to browse or take that history with you.
`agentscroll` gives you one consistent, scriptable view across agents:

- **See** any past conversation as a readable transcript.
- **Search** across every session by keyword.
- **Export** a session to Markdown, JSON, HTML, or plain text.
- **Copy** a rendered session straight to your clipboard.

Compared to existing tools, the niche here is: Python, **CLI-first**
(with a web app planned), **multi-client**, **direct read-only** access to
the live stores (no sync step, no plugins, no upload), and first-class
**export/copy**.

## Install

```bash
pip install -e .            # from a local clone (editable)
# or, once published:
# pip install agentscroll
```

Requires Python 3.10+. The CLI core has **zero runtime dependencies**
(stdlib only). The optional web app needs extras: `pip install -e ".[web]"`.

## Usage

```bash
agentscroll sources                       # which agents are detected
agentscroll list                          # recent sessions, newest first
agentscroll list --source opencode -n 10  # only opencode, 10 rows
agentscroll list --dir myproject          # filter by directory substring
agentscroll list -q "refactor"            # filter by title substring
agentscroll list --since 2026-06-01       # date range (YYYY-MM-DD or ISO)
agentscroll list --until 2026-06-30
agentscroll list --usage                  # show cost + token (in/out) columns
agentscroll list --no-fold                # don't nest subagents under parents
agentscroll list -n 20 --page 2           # pagination (page size = --limit)
agentscroll list --plain                  # disable colour (auto-off when piped)

agentscroll show latest                   # print the most recent transcript
agentscroll show ses_0eae9810 --reasoning # include reasoning blocks
agentscroll show <id> --no-tools          # hide tool calls/outputs

agentscroll search "merge conflict"       # search across all sessions
agentscroll search "ssh" --source opencode --json

agentscroll export latest -f markdown -o session.md
agentscroll export <id> -f html -o session.html
agentscroll export <id> -f json           # to stdout

agentscroll copy latest -f markdown       # copy to clipboard

agentscroll web                           # launch the local web app
agentscroll web -p 9000 --no-browser      # custom port, don't auto-open
agentscroll web --app                     # open in a native desktop window
```

### Web app

`agentscroll web` starts a local, read-only browser UI (FastAPI + a
small vanilla-JS frontend, no build step) bound to `127.0.0.1` by default.

Features:

- **Session list** with clear source filter chips, date filters
  (since / until), and a **home** button (reset everything); loads
  incrementally with **infinite scroll**.
- **Explicit search scope**: a `titles | contents` toggle next to the
  search box. Search session titles, message contents, or both at once
  (combined results are grouped into "title matches" and "content
  matches").
- **Subagents** are collapsed under their parent and expand on demand.
  For Claude Code this includes the per-session **sidechain transcripts**
  (`<session>/subagents/agent-*.jsonl`), titled from each subagent's
  `agentType` + description.
- **Markdown rendering with syntax highlighting** for assistant/user text
  (vendored `marked` + `highlight.js`, no CDN, served locally).
- **Content search** across all sessions (highlighted snippets);
  clicking a hit opens the session at the matching message.
- **Transcript reader** that loads **lazily in windows** so even very
  large sessions (tens of thousands of messages) open instantly without
  freezing; more messages load as you scroll.
- **Find in transcript** (highlight + next/prev), `reasoning` / `tools`
  toggles, token/branch/model metadata, copy-session-id.
- **Export** (Markdown / HTML / JSON) and **copy to clipboard** per
  session.
- **Light / dark theme** (follows the system, with a manual toggle).
- **Keyboard navigation**: `/` focus search, `j` / `k` move through the
  list, `Enter` open, `f` find-in-transcript, `Esc` blur.

Deep links: the open session is reflected in the URL hash
(`#opencode/<id>`), and `?q=<text>` pre-fills a content search.

Install the web extra first: `pip install -e ".[web]"`.

#### Launching without the terminal

You don't have to type the full command every time. After
`pip install agentscroll` you have several options:

- **Short commands on PATH** (installed by pip):
  - `agentscroll-web` -- same as `agentscroll web`
  - `agentscroll-app` -- same as `agentscroll web --app`
- **A double-clickable launcher**: run `agentscroll install-launcher`
  once. It drops the OS-appropriate launcher on your Desktop:
  - macOS: `agentscroll.command` (first run: right-click -> Open). Add
    `--app-bundle` to also create `~/Applications/agentscroll.app` (an
    actual app icon you can double-click or pin to the Dock).
  - Windows: `agentscroll.bat`.
  - Linux: installs `agentscroll.desktop` into your application menu and
    drops `agentscroll.sh` on the Desktop.
  Use `--dest <dir>` to place it somewhere else.
- **Desktop app window**: `agentscroll web --app` opens a native window
  (no browser tab) via `pywebview` (`pip install "agentscroll[app]"`).

All of these run the same local, read-only server; they differ only in
how it's started and displayed. This keeps agentscroll platform-agnostic
(no bundled native binary to maintain per OS). The launcher templates
ship inside the package, so they work for `pip install` users -- not just
source checkouts.

#### How huge sessions stay fast

The web API separates a cheap **metadata** endpoint
(`/api/sessions/{source}/{id}/meta`) from a **windowed messages**
endpoint (`/api/sessions/{source}/{id}/messages?offset&limit`). The
frontend fetches the header first, then pages messages in as you scroll,
so the browser never receives or renders an entire multi-megabyte
transcript at once.

### Selectors

Commands that take a session accept any of:

- a full id (`ses_0eae98104ffe...` or a Claude UUID),
- a unique prefix (`ses_0eae9810`),
- a source-qualified id (`opencode:ses_0eae9810`),
- the keyword `latest`.

### Output formats

`markdown` (`md`), `json`, `html`, `text` (`txt`). Markdown/HTML/text
support `--reasoning` (include the model's thinking) and `--no-tools`
(omit tool calls and their output). JSON is a faithful structured dump
(with bulky raw blobs stripped for readability).

### Subagents, usage, and colour

- **Subagent folding** (default on for `list`): sessions spawned by
  another (opencode `parent_id`; e.g. `@explore` subagents) are nested
  under their parent. Use `--no-fold` to list them flat.
- **Usage columns** (`--usage`): show cost and `tokens in/out`. (opencode
  tracks these; input is dominated by cache reads. Cost may be `$0` when
  your provider does not report it.)
- **Colour**: `list`, `search`, and `show` render with colour when the
  optional `rich` package is installed and output is a terminal; piping
  or `--plain` falls back to plain text. Install with
  `pip install -e ".[rich]"`.

## Sources

| Source | Reads | Location (default) |
|---|---|---|
| `opencode` | SQLite (`session`/`message`/`part`), read-only `mode=ro` | `~/.local/share/opencode/opencode.db` |
| `claudecode` | per-project JSONL transcripts + nested subagent sidechains | `~/.claude/projects/` |

Override locations with environment variables:

- `AGENTSCROLL_OPENCODE_DB` — path to `opencode.db`
- `AGENTSCROLL_CLAUDE_DIR` — path to `~/.claude` or `~/.claude/projects`

## Adding a new agent

Implement the `Source` interface in
`src/agentscroll/sources/base.py` and register the class in
`src/agentscroll/sources/registry.py`. Nothing else needs to change —
the CLI, search, and export all work against the common model.

## Safety

- Opens the opencode database with SQLite `mode=ro` (never creates, never
  writes, safe against a live WAL).
- Reads Claude Code JSONL files read-only.
- A test asserts the opencode DB's modification time is unchanged across
  reads (`tests/test_sources_live.py`).

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT.
