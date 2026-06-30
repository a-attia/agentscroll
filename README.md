# agentscroll

[![CI](https://github.com/a-attia/agentscroll/actions/workflows/ci.yml/badge.svg)](https://github.com/a-attia/agentscroll/actions/workflows/ci.yml)

Browse, search, copy, and export your AI coding-agent sessions from one
local, read-only tool. agentscroll reads the conversation history that
agents like **opencode** and **Claude Code** already keep on disk and gives
you a single, consistent view across them — from a scriptable command line
or a local web app.

Everything is local-first and strictly **read-only**: agentscroll never
modifies, locks for writing, or uploads your data.

> **For AI agents:** read [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
> project conventions. This README is for human readers.

## Contents

- [Why agentscroll](#why-agentscroll)
- [Install](#install)
- [Quick start](#quick-start)
- [The command line](#the-command-line)
- [The web app](#the-web-app)
- [Running it as an app](#running-it-as-an-app)
- [Fast search (optional index)](#fast-search-optional-index)
- [Supported sources](#supported-sources)
- [Configuration](#configuration)
- [Safety](#safety)
- [Development](#development)
- [License](#license)

## Why agentscroll

AI coding agents persist rich session data locally, but each in its own
format and with no convenient way to browse that history or take it with
you. agentscroll fills that gap with four things:

- **See** any past conversation as a readable transcript.
- **Search** across every session by keyword (title or full text).
- **Export** a session to Markdown, JSON, HTML, or plain text.
- **Copy** a message or a whole session straight to your clipboard.

Its niche among similar tools: pure Python, works equally from the
**CLI and a web UI**, reads **multiple agents** directly from their live
on-disk stores (no sync step, no plugins, no upload), and treats
**export and copy** as first-class.

## Install

```bash
pip install -e ".[web]"     # from a local clone: CLI + web app
```

agentscroll is not yet published to PyPI; install from a clone for now.
Requires Python 3.10+. The bare CLI has **no runtime dependencies**
(standard library only); optional features come from extras:

- `".[web]"` — the local web app (FastAPI, uvicorn) and the native app
  window (pywebview).
- `".[rich]"` — coloured terminal output.
- `".[dev]"` — test and lint tooling.

## Quick start

```bash
agentscroll doctor          # what was detected on this machine?
agentscroll list            # recent sessions, newest first
agentscroll show latest     # print the most recent transcript
agentscroll web             # open the browser UI
```

`agentscroll doctor` is the best first command: it reports which agents
were found, how many sessions each has, and which optional features are
available.

## The command line

The CLI is organised around a few verbs. Commands that operate on a single
session accept a **selector**: a full id, a unique prefix, a
source-qualified id (`opencode:ses_0eae9810`), or the keyword `latest`.

### Listing and viewing

```bash
agentscroll list --source opencode -n 10   # one source, 10 rows
agentscroll list --dir myproject           # filter by directory substring
agentscroll list -q "refactor"             # filter by title substring
agentscroll list --since 2026-06-01 --until 2026-06-30   # date range
agentscroll list --usage                   # add cost + token (in/out) columns
agentscroll list -n 20 --page 2            # pagination (page size = --limit)

agentscroll show latest --reasoning        # include the model's thinking
agentscroll show <selector> --no-tools     # hide tool calls and output
```

By default, subagent sessions (for example opencode `@explore` subagents)
are **folded** under their parent; pass `--no-fold` to list them flat.
Output is coloured when the `rich` extra is installed and the output is a
terminal; piping, or `--plain`, falls back to plain text.

### Searching

```bash
agentscroll search "merge conflict"        # full-text across all sessions
agentscroll search "ssh" --source opencode --json
```

Search scans message text across sessions. On a large history you can make
it near-instant with an [optional index](#fast-search-optional-index).

### Exporting and copying

```bash
agentscroll export latest -f markdown -o session.md
agentscroll export <selector> -f html -o session.html
agentscroll export <selector> -f json      # to stdout
agentscroll copy latest -f markdown        # render and copy to the clipboard
```

The formats are `markdown` (`md`), `json`, `html`, and `text` (`txt`).
Markdown, HTML, and text honour `--reasoning` (include the model's
thinking) and `--no-tools` (omit tool calls and their output); JSON is a
faithful structured dump with bulky raw blobs stripped for readability.
Exported HTML and Markdown render the assistant's Markdown with syntax-
highlighted code, and the HTML is a self-contained file that prints well.

### Stats and resume

```bash
agentscroll stats                          # totals, by-source + top projects
agentscroll resume latest                  # print the native resume command
agentscroll resume <selector> --copy       # ...and copy it to the clipboard
```

`stats` aggregates session counts, message/token/cost totals, and your
busiest projects. `resume` prints the command to continue a session in its
own agent (for example `opencode --session <id>` or `claude --resume <id>`),
with a `cd` into the session's project directory.

## The web app

`agentscroll web` starts a local, read-only browser UI — FastAPI plus a
small vanilla-JavaScript frontend with no build step — bound to
`127.0.0.1`. Open it with `agentscroll web` (a browser tab),
`agentscroll web --window` (a standalone browser window), or
`agentscroll web --app` (a native desktop window; see
[Running it as an app](#running-it-as-an-app)).

What it offers:

- A **session list** with source-filter chips, date filters, and a
  **home** button to reset everything; it loads incrementally as you
  scroll.
- An explicit **search scope** toggle — search session **titles**, message
  **contents**, or both at once (combined results are grouped).
- **Subagents** collapsed under their parent, expandable on demand
  (including Claude Code's nested sidechain transcripts).
- A **transcript reader** with a frozen header and a scrolling message
  body, **Markdown rendering with syntax highlighting**, in-transcript
  find, reasoning/tools toggles, and per-message and per-session copy.
- **Export** (Markdown / HTML / JSON), **print**, a **light/dark theme**,
  and **keyboard navigation** (`/` search, `j`/`k` move, `Enter` open,
  `f` find, `Esc` blur).

Large transcripts open instantly because the app loads a session's header
first and then pages messages in as you scroll, rather than transferring an
entire multi-megabyte transcript at once. Deep links work too: the open
session is reflected in the URL hash (`#opencode/<id>`), and `?q=<text>`
pre-fills a content search.

## Running it as an app

You don't have to type a command every time. After `pip install ".[web]"`:

- **Short commands** are on your `PATH`: `agentscroll-web` (a browser tab)
  and `agentscroll-app` (a native window).
- **A double-clickable launcher** is one command away:

  ```bash
  agentscroll install-launcher              # drops a launcher on your Desktop
  agentscroll install-launcher --app-bundle # macOS: also make an .app icon
  ```

  This installs the launcher appropriate to your OS — `agentscroll.command`
  on macOS (with `--app-bundle`, an `~/Applications/agentscroll.app` icon),
  `agentscroll.bat` on Windows, and an application-menu entry plus
  `agentscroll.sh` on Linux. Use `--dest <dir>` to place it elsewhere.

The launchers open a **native window** via pywebview when it is available:
no browser tab, no terminal, and **closing the window stops the server and
frees the port**. On a system where pywebview cannot run (for example a
headless Linux box without a GTK/Qt WebKit backend), agentscroll falls back
to a standalone browser window that auto-stops the server shortly after the
window closes. All of this behaviour is decided in Python, so the launcher
scripts stay free of OS-specific assumptions and ship inside the package
for `pip install` users.

## Fast search (optional index)

By default, search is a lexical scan over your live data: zero setup,
always correct, but its cost grows with the size of your history. For a
large corpus, build an optional full-text index:

```bash
agentscroll index            # one-time build; re-run to update (incremental)
agentscroll index --stats    # show what's indexed
agentscroll index --clear    # delete the index
```

The index is a separate SQLite FTS5 database at
`~/.cache/agentscroll/index.db` (override with `AGENTSCROLL_INDEX`). It is
derived and disposable: your source data is never modified, and deleting
the index simply reverts to the lexical scan. Re-running `index` only
re-processes new or changed sessions and prunes deleted ones; the web app
also refreshes it in the background on startup when it is stale.

Once built, both the CLI and the web app use it automatically, turning a
multi-second query into a few milliseconds. If your Python's SQLite was
built without FTS5, `index` says so and search keeps working without it.

## Supported sources

| Source       | Reads                                                       | Default location                       |
|:-------------|:-----------------------------------------------------------|:---------------------------------------|
| `opencode`   | SQLite (`session` / `message` / `part`), read-only         | `~/.local/share/opencode/opencode.db`  |
| `claudecode` | per-project JSONL transcripts + nested subagent sidechains  | `~/.claude/projects/`                   |
| `codex`      | per-session `rollout-*.jsonl` rollouts                      | `~/.codex/sessions/`                    |
| `aider`      | per-project `.aider.chat.history.md` Markdown logs          | searched from the working directory     |

More agents (Gemini CLI, Zed, VS Code Copilot Chat, GitHub Copilot CLI) are
researched and queued — see [`ROADMAP.md`](ROADMAP.md).

Adding another agent is a small, self-contained change: implement the
`Source` interface in `src/agentscroll/sources/base.py` and register it in
`src/agentscroll/sources/registry.py`. The CLI, search, export, web app,
and index all work against the common model automatically — see the
opencode (SQLite) and Claude Code (JSONL) adapters as references, and
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the conventions.

## Configuration

agentscroll reads each agent's data from its default location, but you can
point it elsewhere, and you can control how the web server binds:

| Variable                  | Purpose                                                      |
|:--------------------------|:------------------------------------------------------------|
| `AGENTSCROLL_OPENCODE_DB` | path to `opencode.db`                                       |
| `AGENTSCROLL_CLAUDE_DIR`  | path to `~/.claude` or `~/.claude/projects`                |
| `AGENTSCROLL_PORT`        | web server port (default `8765`; or use `--port`)           |
| `AGENTSCROLL_HOST`        | web server bind host (default `127.0.0.1`; or use `--host`) |
| `AGENTSCROLL_INDEX`       | path to the search index database                          |

The web server defaults to `127.0.0.1`. If the chosen port is busy,
agentscroll automatically picks the next free one (`--strict-port` fails
instead). Binding to a non-loopback host prints a warning, since the
read-only API is unauthenticated.

## Safety

agentscroll is read-only by design, and the design is enforced:

- The opencode SQLite database is opened with `mode=ro` — it is never
  created or written, and reads are safe against a live write-ahead log.
- Claude Code JSONL files are read as read-only.
- A test asserts the opencode database's modification time is unchanged
  across reads (`tests/test_sources_live.py`).
- The web app binds to localhost, rejects unexpected `Host` headers (a
  DNS-rebinding guard), and sanitizes rendered transcript content.

## Development

```bash
pip install -e ".[web,dev]"
pytest -q             # tests
ruff check src tests  # lint
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for project conventions (the
read-only invariant, stdlib-first dependencies, platform-agnostic code,
and how to add a new agent source) and [`CHANGELOG.md`](CHANGELOG.md) for
what has landed so far.

## License

MIT — see [`LICENSE`](LICENSE).
