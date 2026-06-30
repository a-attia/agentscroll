# Roadmap

Planned work for agentscroll, with the research behind it so the next
contributor doesn't have to re-derive it. This is a living document; the
current state of what *is* built lives in [`CHANGELOG.md`](CHANGELOG.md).

## Planned source adapters

agentscroll currently reads **opencode**, **Claude Code**, **Codex**, and
**Aider**. The agents below were researched (formats, locations,
feasibility) but not yet implemented. The table records the verdict so the
effort and risk are known up front.

| Agent | Local store | Format | Transcripts? | Feasibility | Verdict |
|:------|:------------|:-------|:-------------|:------------|:--------|
| **Gemini CLI** | `~/.gemini/tmp/<project_hash>/chats/` (checkpoints under `checkpoints/`) | JSON per session | Yes — full (prompts, responses, tool I/O, tokens) | Easy–Medium | **Tier 1 — do next.** Best-documented; closest analog to the Claude Code JSONL adapter. Caveats: 30-day rolling retention by default; `<project_hash>` must map back to a project root. |
| **Zed** | `<data_dir>/threads/threads.db` (macOS `~/Library/Application Support/Zed`) | SQLite; `data` BLOB = zstd-compressed JSON | Yes — roles, text, thinking, tools, tokens | Medium | **Tier 1.** Adds an editor surface. Needs a zstd dependency (optional extra) and dual-version JSON handling (`0.3.0` + legacy). Schema is open-source and re-verifiable per release. |
| **VS Code Copilot Chat** | `<vscode-user>/workspaceStorage/<hash>/chatSessions/*.json` | JSON per session | Yes — requests/responses, parts, tool calls | Medium | **Tier 2 — best-effort.** High reach, but the schema is internal and churns across releases. Budget for version tolerance + workspace-hash→project mapping. |
| **GitHub Copilot CLI** | `~/.copilot/session-state/` | JSON state files | Partial→Yes (auto-compaction can lose detail) | Medium | **Tier 2 — best-effort.** Undocumented on disk; confirm the shape on a current build first. Exclude the legacy `gh copilot` Suggest/Explain extension (no transcripts). |
| **Cursor** | `state.vscdb` SQLite (VS Code-style app data) | SQLite key-value blobs | Partial | Hard | **Tier 2/3.** Chat is buried in a key-value DB; brittle to reverse-engineer and version-sensitive. |
| **Windsurf / Cascade** | `~/.codeium/windsurf/memories/` (rules/memories only) | Markdown | No (transcripts are cloud-side) | Not feasible | **Skip.** Conversations are not a documented local artifact; only distilled memories/rules live on disk. |

### Implementing one

Each adapter is a `Source` subclass in `src/agentscroll/sources/` registered
in `registry.py`; see [`CONTRIBUTING.md`](CONTRIBUTING.md) and the existing
JSONL (`codex.py`, `claudecode.py`) and SQLite (`opencode.py`) adapters as
references. The same checklist applies to every new adapter:

- read-only access only (no writes, no write-locks);
- tolerant parsing (skip malformed records; degrade, don't crash);
- a `resume_command` override if the agent supports by-id resume;
- synthetic-fixture tests, since contributor machines won't all have the
  agent's data.

## Other ideas

- **`tail` / `watch`**: live-follow the most recently active session as it
  grows.
- **Per-source counts on the web filter chips** (deferred: needs a cheap
  count; opencode's full enumeration is currently too slow per page load).
- **Disk-usage reporting** for each session store (read-only).

---

*Living document. Verdicts above reflect a format-feasibility review; the
on-disk formats of the Tier 2/3 agents are internal and may have changed —
re-verify against a current build before implementing.*
