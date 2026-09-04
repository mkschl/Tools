# CLAUDE.md — agent-memory-mcp

This file provides guidance to Claude Code when working in this repository.

## Commands

```sh
# Install / sync dependencies
uv sync
uv sync --extra dev   # includes pytest

# Run the MCP server
uv run agent-memory-mcp

# Test
uv run pytest

# Lint
uv run ruff check agent_memory_mcp/
uv run ruff format agent_memory_mcp/
```

## Test structure

Tests live in `tests/test_storage.py` and cover all storage functions using
`tmp_path` (via a `monkeypatch` fixture on `STORAGE_ROOT`) so nothing touches
`~/.agent-memory/` during the test run:

- `decision_log`, `decision_read`
- `kanban_add`, `kanban_move`, `kanban_read`
- `_parse_board` (title, columns, cards, metadata, steps, description blocks)

`server.py` (MCP tool wrappers) is not unit-tested — verify by registering in
MCPGateway and calling tools via Claude Code or lmstudio-code-cli.

## Purpose

A minimal MCP server that provides shared persistent memory across all tools
connected to MCPGateway (Claude Code, lmstudio-code-cli, and future tools).
It is not an agent. It has no autonomous behaviour. It is a neutral persistence
layer with three concepts: decisions, kanban boards, and queries.

## Storage Layout

All data lives under `~/.agent-memory/`:

```text
~/.agent-memory/
  decisions/<project>/YYYY-MM-DD.md   # Chronological decision log
  kanban/<project>.md                  # Per-project kanban board
  notes/<project>/<key>.md             # Per-project notes
```

Files are plain markdown. They can be opened and edited directly in VS Code.
Kanban files are compatible with the holooooo.markdown-kanban VS Code extension.

### Project identity and naming convention

`project` is free text (e.g. `JobSearch`, `job-search`, `Job Search`). Rather
than normalize it into a lowercased slug used as the actual directory name
(and track the original spelling in a side file), each path helper
(`_kanban_path`, `_notes_dir`, `_decisions_dir` in `storage.py`) *scans* its
subsystem directory for an existing entry that normalizes to the same slug —
casing and separators ignored — and reuses it verbatim if found. A brand-new
project is created using the exact spelling given. The on-disk name always
**is** the display name; there's no registry to keep in sync or go stale.

One consequence: "first-seen spelling" is tracked per subsystem, not
globally. If a board is created as `JobSearch` and a note is later written
under `job-search`, the notes directory is named `job-search` — there's
nothing to say it should match the board's casing. What matters is that a
*second* call under any casing variant reuses that same `job-search`
directory instead of creating yet another sibling.

**Naming convention: PascalCase project names** (`JobSearch`, `Homelab`,
`DockerProjects`) — normalization makes it safe against typos and casing
slips, but staying consistent still keeps directory names readable. Call
`project_list` before writing to a project name you're not sure already
exists; it groups near-duplicate spellings together and flags collisions.

Writing to an unknown project (via `decision_log` or `note_write`) fails with
a suggestion (or a prompt to pass `create=true`) rather than silently
creating a new project — this is what prevents a typo or casing slip from
quietly forking a project's data into a sibling directory. `kanban_create` is
still the explicit way to create a new board.

Pre-existing colliding directories from before this resolution behavior
existed (e.g. `JobSearch` and `job-search` as two separate directories) were
already found and merged by hand — there's nothing left to migrate. If a
similar split ever reappears (e.g. from a sync conflict or a manual rename
outside the server), `project_list` will surface it as a collision; merge it
by moving files into one directory manually, since the normalized-name scan
prevents new writes from creating another one but doesn't retroactively fix
what's already split.

## MCP Tools

The server exposes exactly these tools — nothing more.

### Projects

- `project_list()` — enumerates every project across boards, notes, and
  decisions, with counts, grouped by normalized slug and flagged when two
  raw names collide. Call this before writing to a project name you're not
  sure already exists.
- `project_summary(project, days?)` — the documented entry point for "show me
  everything about this project": board, recent decisions, and notes together.

### Decisions

- `decision_log(project, summary, reasoning, create?)` — appends a dated
  entry to `~/.agent-memory/decisions/<project>/YYYY-MM-DD.md`. Fails on
  an unknown project unless `create=true` is passed.
- `decision_read(project, days?)` — returns recent entries for a project.
  `days` defaults to 7. If `project` is omitted returns entries across all
  projects.

### Kanban

- `kanban_add(project, column, title, notes?, ...)` — adds a card to the
  specified column. Fails if the board doesn't exist yet — call
  `kanban_create` first. `notes` is an optional comma-separated list of note
  keys already written for this project (via `note_write`); validated
  against `note_list`, so a card can't reference a note that doesn't exist.
- `kanban_move(project, title, to_column)` — moves an existing card to a
  different column.
- `kanban_read(project)` — returns the full board for a project, with the
  project's note keys appended so opening a board shows what else belongs to
  it.

### Notes

- `note_write(project, key, content, create?)` — writes (creates or
  overwrites) a note. Fails on an unknown project unless `create=true` is
  passed.
- `note_read(project, key)` / `note_list(project)` / `note_delete(project, key)`

### Markdown linting

Not a tool — an internal quality pass. `note_write` runs the note content
through `markdownlint --fix` before saving, and appends any remaining
(unfixable) lint issues to its result string. Decisions and kanban files are
template-rendered, not free-form author content, so they are not linted.

Implemented in `agent_memory_mcp/markdownlint.py`, which shells out to the
system `markdownlint` binary (markdownlint-cli, npm) via `subprocess`. If the
binary isn't on `PATH` (`shutil.which` check), linting is skipped
gracefully — writes still succeed, just without a lint pass. Install it with
`npm install -g markdownlint-cli` to enable it.

Rules are configured in `.markdownlint.jsonc` at the repo root — edit it to
ignore specific `MD0xx` rules (uncomment the corresponding line; each one
already carries its own leading comma so you can uncomment any subset in any
order). The same file is picked up by editor extensions (e.g. VS Code's
markdownlint extension) when this repo is open, so notes get consistent
results everywhere. Point `AGENT_MEMORY_MARKDOWNLINT_CONFIG` at a different
config file to override the location.

## Architecture

The server uses the MCP stdio transport, consistent with kubectl-mcp and
parallels-mcp. MCPGateway launches it as a subprocess and acts as the single
shared hub — all clients route through MCPGateway, so the subprocess is
effectively shared across callers.

All file I/O is synchronous. No database, no external dependencies beyond
the MCP SDK. The storage path `~/.agent-memory/` is resolved at startup and
is not configurable — simplicity is intentional.

Kanban files follow the markdown-kanban format exactly. Columns are `## Column`
headings. Cards are `### Card Title` under their column with optional body text
for notes.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `AGENT_MEMORY_DIR` | `~/.agent-memory` | Storage root — point to an iCloud/Dropbox folder to sync across machines |

Set it in the MCPGateway entry for this server, e.g.:
```json
{"env": {"AGENT_MEMORY_DIR": "/Users/you/Library/Mobile Documents/com~apple~CloudDocs/agent-memory"}}`
```

## Conventions

- Python 3.14+, managed with `uv`
- `structlog` for logging
- `ruff` for linting and formatting
- No sandboxing — the server runs with full filesystem access under the user
  account, consistent with MCPGateway and lmstudio-code-cli
- Follows the same repo structure as other tools in this tools repository
