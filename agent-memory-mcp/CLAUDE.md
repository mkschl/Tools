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
```

Files are plain markdown. They can be opened and edited directly in VS Code.
Kanban files are compatible with the holooooo.markdown-kanban VS Code extension.

## MCP Tools

The server exposes exactly these tools — nothing more.

### Decisions

- `decision_log(project, summary, reasoning)` — appends a dated entry to
  `~/.agent-memory/decisions/<project>/YYYY-MM-DD.md`. Creates the file and
  directory if they do not exist.
- `decision_read(project, days?)` — returns recent entries for a project.
  `days` defaults to 7. If `project` is omitted returns entries across all
  projects.

### Kanban

- `kanban_add(project, column, title, notes?)` — adds a card to the specified
  column. Creates the board file if it does not exist with default columns:
  `Backlog`, `In Progress`, `Done`.
- `kanban_move(project, title, to_column)` — moves an existing card to a
  different column.
- `kanban_read(project)` — returns the full board for a project.

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
