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

`server.py`'s individual `@mcp.tool()` wrappers (the thin storage-call +
`log.info` glue) are not unit-tested — verify those by registering in
MCPGateway and calling tools via Claude Code or lmstudio-code-cli. The one
piece of real logic in `server.py`, the `_run` thread-offloading/timeout
wrapper (see Architecture below), does have its own tests in
`tests/test_server.py`, run with plain `asyncio.run()` — no pytest-asyncio
plugin needed.

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

All file I/O in `storage.py` is synchronous. No database, no external
dependencies beyond the MCP SDK. The storage path `~/.agent-memory/` is
resolved at startup and is not configurable — simplicity is intentional.
Every write goes through `_atomic_write_text` (temp file + `os.replace()`)
rather than a plain `Path.write_text()` — see "Why `_run` also takes a
`project` lock" below for why that matters even though it looks unrelated to
concurrency at a glance.

### Why every tool wrapper in server.py is `async` and calls `_run`

`AGENT_MEMORY_DIR` is commonly pointed at an iCloud/Dropbox-synced folder (to
sync across machines) — a plain file read/write there can stall for minutes
if the sync client needs to fetch an evicted local copy or is otherwise slow.
FastMCP calls a synchronous tool function directly on its single asyncio
event loop with **no thread-pool offloading** (confirmed by reading
`mcp/server/fastmcp/utilities/func_metadata.py`'s
`call_fn_with_arg_validation`: it just does `fn(**arguments)` for a sync
`fn`). That means a single stalled synchronous call previously froze the
*entire* server, not just that one request.

This was observed directly: `kanban_update_card` didn't return for 4 minutes,
and a `kanban_read` issued around the same time also hung, then the server
"recovered on its own" once the underlying I/O call finally returned. There
is no lock anywhere in `storage.py` — a blocked event loop produces the
identical symptom without one, which is what actually happened.

The fix (`_run` in `server.py`) offloads each tool's blocking call to a
worker thread via `anyio.to_thread.run_sync`, so a stall in one request
doesn't block any other concurrent request, plus wraps it in a timeout
(`_SLOW_OPERATION_TIMEOUT_SECONDS`) so a genuinely stuck call returns an
informative message instead of hanging silently — the underlying thread
keeps running to completion in the background regardless (Python can't
forcibly kill a blocked thread), so a write that eventually does land still
lands, just without the caller having waited for it. `tests/test_server.py`
has a regression test asserting a slow call doesn't block a concurrent fast
one.

### Why `_run` also takes a `project` lock

Every `storage.py` kanban/note/decision function does a non-atomic
read-modify-write against one project's files (e.g. `kanban_update_card`:
read the board, mutate it in memory, write it back). Before `_run` existed,
FastMCP's single event loop made this safe *by accident* — two tool calls
could never actually run at the same time, so a read-modify-write always
completed before the next one started. Thread-offloading removes that
accidental serialization: two concurrent calls touching the same project can
now genuinely race, both reading the same starting state and the second
write silently clobbering the first. Reproduced directly during review: two
concurrent `kanban_add` calls to one board, one card vanished with no error
to either caller.

`_run(func, *args, project=...)` takes one or more `asyncio.Lock`s keyed by
`storage._slugify(project)` — the same normalization `storage.py` itself
uses to resolve a project name, so `'JobSearch'` and `'job-search'` share one
lock rather than getting two and still racing. Only **write**-triggering
tool wrappers pass `project=` (`decision_log`, `kanban_create`,
`kanban_add`, `kanban_update_card`, `note_write`, ...) — read-only tools
(`kanban_read`, `note_list`, `decision_read`, `kanban_search`,
`project_summary`, ...) call `_run` with no `project` kwarg at all and take
no lock.

An earlier version of this fix locked reads too, on the theory that it would
stop a read from ever observing a half-written file mid-mutation. That
traded one bug for a worse one: since the lock has no maximum hold time, a
*permanently* wedged write (not just a slow one) meant every subsequent
read **and** write for that project timed out every 25s, forever — strictly
worse than the pre-lock behavior, where a stuck write didn't block reads at
all. The actual fix for the half-written-file concern belongs at the
storage layer, not the lock: every write in `storage.py` now goes through
`_atomic_write_text` (write to a same-directory temp file, then
`os.replace()`), so a lock-free reader can only ever observe the complete
old file or the complete new one, never bytes from a write in progress.
That makes it safe for reads to skip the lock entirely — they no longer
need it for correctness, only writes serializing against other writes do.
(There's still a narrow, harmless TOCTOU window a lock-free read can hit if
a file is deleted/renamed by a concurrent write between resolving its path
and reading it — `kanban_read`, `note_read`, `decision_read`, and
`kanban_search` all catch `FileNotFoundError` around that read and treat it
as "not found," the same outcome a slightly-earlier read would have gotten
anyway, rather than letting the exception surface as a crash.)

Global, multi-project calls (`kanban_list`, `project_list`) also pass no
`project` and take no lock, since they don't do a per-project
read-modify-write at all.

`kanban_rename` passes a tuple, `project=(project, new_name)`, locking both
its source and destination — locking only the source (an earlier version of
this fix) left the destination name completely unprotected: two concurrent
`kanban_rename` calls into the same `new_name` both passed the "does it
already exist" check (neither held a lock on it) and the second clobbered
the first. `_locks_for` resolves a `project` tuple to its distinct
normalized slugs, sorted before acquiring — that global sort is what
prevents a different deadlock: two calls each locking two of the same
projects but requesting them in opposite order would otherwise be able to
each hold the lock the other wants next. A `project` tuple whose entries
share a slug (e.g. a pure recasing rename, `('JobSearch', 'jobsearch')`)
dedupes to one lock rather than trying to acquire the same non-reentrant
`asyncio.Lock` twice.

The lock is held for the operation's true duration, not just for as long as
the caller waits: `_run` wraps the call in `asyncio.create_task` +
`asyncio.shield`, so a timeout only stops the *caller* from waiting longer —
it does not cancel the underlying task or release its lock early. Releasing
the lock on timeout would let a subsequent call for the same project start a
new read-modify-write cycle while the first write is still in flight,
reintroducing the identical race across a timeout-then-retry sequence.

`_call_in_thread` still passes `abandon_on_cancel=True` to
`anyio.to_thread.run_sync` — that only matters for a *genuine* external
cancellation of the task (e.g. process shutdown cancelling every outstanding
asyncio task), since `asyncio.shield` already stops `_run`'s own timeout
from ever reaching the task as a cancellation. On a real shutdown, letting
the thread detach immediately (rather than blocking exit until it finishes)
is the right trade: the whole process — and `_project_locks` along with it —
is going away, so there's no future caller left in that process to exploit
the lock being released early.

If a call's task is still running when its caller times out, and it later
raises an exception, that exception would otherwise vanish silently (a bare
"Task exception was never retrieved" warning with no context). The
`TimeoutError` branch in `_run` attaches `_log_late_completion` as a
done-callback specifically for this case, so a delayed failure logs
`delayed_operation_failed` and a delayed success logs
`delayed_operation_completed` — visible in the server's logs even though the
original caller already moved on.

`tests/test_server.py` covers: same-project calls serialize without losing
an update, normalized-spelling variants share one lock, different projects
still run fully concurrently, a timed-out call keeps its lock until it
genuinely finishes, a call made with no `project=` never waits on any lock
(even behind an arbitrarily slow same-named write), and a late
success/failure after a timeout gets logged instead of disappearing.

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
