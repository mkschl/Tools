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

Nested folders, standalone notes and `project_move` are covered by
`tests/test_project_paths.py`: path validation (traversal, symlink escape,
empty segments, depth limit, reserved and dot names), single names staying
free text, project/folder detection and conflicts in both directions,
per-segment name matching, `project_list` (tree, kind wording, `folder` filter,
collisions), `project_move` (dry run, success, every refusal, emptied folders
removed, concurrent moves into one target, board links still resolving),
`kanban_rename`, standalone notes, and the refusal of older layouts. The
migration command has its own tests in `tools/migrate-layout-cli/tests/`; a
bare `uv run pytest` runs both.

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

All data lives under `~/.agent-memory/` (or `AGENT_MEMORY_DIR`). The layout is
**project-first**: every project is one directory below `projects/` that holds
its board, notes and decisions, and projects can be grouped in nested
folders. A separate top-level `notes/` holds standalone notes that belong to
no project.

```text
~/.agent-memory/
  projects/
    JobSearch/                         a folder (groups projects)
      Overview/                        a project
        board.md                       kanban board
        notes/<key>.md                 notes
        decisions/YYYY-MM-DD.md        chronological decision log
      eigenblue/                       a notes-only project
        notes/<key>.md
    Work/AzureAIConsultantTraining/    a board-only project
      board.md
  notes/<key>.md                       standalone notes (no project)
```

Files are plain markdown. They can be opened and edited directly in VS Code.
`board.md` is compatible with the holooooo.markdown-kanban VS Code extension.

**Why project-first:** an earlier version kept the three kinds of data in
three top-level trees (`kanban/`, `notes/`, `decisions/`). A project then
could not be seen as one thing, and moving it meant touching three places.
With one directory per project a move is a single rename, a project's
contents are visible at a glance, and board links to notes are the same at
every depth. The root holds only `projects/` and `notes/` (plus hidden
entries such as `.vscode`), and nothing is ever created or moved outside it.

**Standalone notes:** the note tools (`note_write`, `note_read`, `note_list`,
`note_delete`) take an empty `project` to work on `notes/` instead of a
project's own notes. They are plain files (no sub-folders), keys follow the
same rules, and they have their own lock. `project_list` mentions how many
exist.

### Projects and folders

- A **project** is a directory with a `board.md`, a `notes/` or a
  `decisions/` entry; any of them may be missing. A **folder** is a directory
  that only holds other directories. A path is a folder or a project, never
  both, and a project has no child projects. Creating one where the other
  exists raises `ProjectPathError`, which `_run` returns as a message.
- A project is a path of 1 to `AGENT_MEMORY_MAX_DEPTH` (default 5) segments
  separated by `/`, for example `Work/AzureAIConsultantTraining`.
- Segments of a nested path may contain letters, digits, `.`, `_` and `-`
  (no spaces, no empty segment, no `.`/`..`, no leading `.`, no `.md` ending).
  A single segment stays free text as before, so `Job Search` works.
- `board`, `notes` and `decisions` are reserved at every level (inside
  `projects/`).
- Names are matched by slug **per segment** (`_project_slug`), so `health`
  and `Health` are one folder and `Work/Example` is not `WorkExample`. The
  first-seen spelling of each segment is reused.
- Every resolved path must stay below the storage root (symlinks included).
  Entries starting with `.` (`.vscode`, `.markdownlint.json`) are ignored.
- Note keys must be plain names: no `/`, no leading `.`.
- Boards link to notes with `notes/<key>.md`, the same at every depth.
- Folders are created implicitly when a project is created below them, and
  removed again when they become empty after a move or a delete.
- `project_move(source, target, dry_run=True)` is one directory rename, so it
  is atomic: the project is either at the old or at the new path. It fails if
  the target exists, is a folder or lies below a project, or if the source is
  a folder. A dry run lists the kinds present, the note keys and the decision
  dates, never their contents. It locks both names (sorted, like
  `kanban_rename`). The board title is updated afterwards when it still
  carries the old name (cosmetic, never undoes the move).
- `kanban_rename(project, new_name)` renames the last segment inside the same
  folder (re-casing goes through a temporary name) and points `/` in the new
  name to `project_move`.
- `project_list` prints a tree and says in words what each project has
  (`board only`, `notes only (2)`, `board + notes (5) + decisions (1)`); a
  project that also holds other projects is flagged.

### Older layouts

The root may contain only `projects/` and `notes/` (plus hidden entries). If
it also has `kanban/` or `decisions/` with visible entries, a `notes/` with
sub-directories (the type-first layout), or project folders directly at the
top, every tool raises `LegacyLayoutError` ("run the migration first") and
writes nothing; the server logs a warning at startup. This keeps old and new
data from ever mixing. A top-level `notes/` that holds only files is the
standalone notes, and a leftover `kanban/.markdownlint.json` (hidden entries
only) does not count.

### Layout migration

`tools/migrate-layout-cli` brought the real storage from the type-first layout
(and the interim "projects at the root" layout) to the current one. It was run
on 2026-10-14 and can be deleted once the backups are no longer needed. Dry
run is the default; `--apply` copies the whole root to a timestamped
`<root>-backup-…` directory first.

```bash
uv run --project tools/migrate-layout-cli migrate-layout \
  --root "$AGENT_MEMORY_DIR" --mapping tools/migrate-layout-cli/mapping.toml
```

- **Type-first to current:** each project is assembled from the three trees and
  written to `projects/<mapped path>` (board, notes, decisions; the board's
  note links are rewritten from `../notes/<name>/<key>.md` to
  `notes/<key>.md`). The mapping is a TOML file (`[projects]`, old name to new
  path), so no project name is hardcoded in the source. Projects missing from
  it keep their path. Empty directories are dropped.
- **Projects at the root to current:** every project directory or folder at
  the top of the root is moved into `projects/` unchanged; the mapping is not
  used. It refuses when `projects/` already holds something.
- **Both at once** is refused; migrate one layout at a time.
- **A name that was both a project and a folder** (`JobSearch`: a board and a
  note directly in `notes/JobSearch/`, plus sub-folders of notes) is read as
  the project made of its own files and as one project per sub-folder. The
  mapping must say where the own files go (`JobSearch = "JobSearch/Overview"`),
  otherwise the command refuses.
- It also refuses duplicate or existing targets and invalid or reserved names,
  prints note keys and decision dates only (never contents; the health notes
  are only moved), and after `--apply` checks that the file count is unchanged
  and reports anything left in the old directories.
- After a migration: confirm `project_list` against the backup, refresh the
  MCPGateway tool list and restart the server, and update anything that still
  mentions the old paths (`.vscode/settings.json` and `.markdownlint.json` in
  the storage root, other `CLAUDE.md` or memory files, scripts that call the
  tools).

### Design decisions

- The board file is `board.md`; the default depth limit is 5.
- A path is a folder or a project, never both, and a project has no child
  projects. The server checks this on every create and move; a hand-made mix
  shows up in `project_list` with a warning.
- Single-segment names stay free text (`Job Search` keeps working); the strict
  segment rules apply to nested paths only.
- Standalone notes are flat files in the top-level `notes/`, reached with an
  empty `project` argument, so notes can live apart from projects.
- Older layouts are refused, not read.
- `kanban_rename` keeps its own code (it also handles re-casing) instead of
  sharing the move helper; merging them is a possible follow-up.
- Real layout choices made while migrating: `Homelab` stays a project,
  `DockerProjects` lives under `Tech/`, `JobSearch` is a folder whose old
  board and `index.md` became the project `JobSearch/Overview`, the old
  `health` project was merged into `Diabetes` (now `Health/Diabetes`), and
  `TemplateAppProject` lives under `Templates/` (example projects, if wanted
  later, belong in a sibling folder `Examples/`).

### Project identity and naming convention

`project` is free text (e.g. `JobSearch`, `job-search`, `Job Search`) or a
path. Rather than normalize it into a lowercased slug used as the actual
directory name (and track the original spelling in a side file),
`_project_dir` in `storage.py` *scans* each directory level for an existing
entry that normalizes to the same slug — casing and separators ignored — and
reuses it verbatim if found. A brand-new project is created using the exact
spelling given. The on-disk name always **is** the display name; there's no
registry to keep in sync or go stale. Because a project is one directory,
the first-seen spelling is the same for its board, notes and decisions.

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

If a split ever appears (e.g. from a sync conflict or a manual rename outside
the server), `project_list` surfaces it as a collision; merge it by moving
files into one directory manually, since the normalized-name scan prevents
new writes from creating another one but doesn't retroactively fix what's
already split.

## MCP Tools

The server exposes exactly these tools — nothing more.

### Projects

- `project_list(folder?)` — enumerates every project across boards, notes, and
  decisions as a tree grouped by folder, with counts, grouped by normalized
  slug and flagged when two raw names collide. `folder` limits it to one
  folder. Call this before writing to a project name you're not sure already
  exists.
- `folder_list()` — folders only, with the number of projects below each.
- `project_summary(project, days?)` — the documented entry point for "show me
  everything about this project": board, recent decisions, and notes together.
  For a folder path it returns the summaries of the projects below it.
- `project_move(source, target, dry_run?)` — moves a project (board, notes and
  decisions together) to another path in one rename. Dry run by default.

### Decisions

- `decision_log(project, summary, reasoning, create?)` — appends a dated
  entry to `projects/<project>/decisions/YYYY-MM-DD.md`. Fails on
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
- `kanban_list()` — the projects that have a board, as paths.
- `kanban_search(query, project?)` — results are labelled with the project
  path.
- `kanban_rename(project, new_name)` — renames the last segment of a project
  inside its folder (board, notes and decisions go with it).

### Notes

- `note_write(project, key, content, create?)` — writes (creates or
  overwrites) a note. Fails on an unknown project unless `create=true` is
  passed. An empty `project` writes a standalone note to the top-level
  `notes/` (and `create` has no meaning there).
- `note_read(project, key)` / `note_list(project)` / `note_delete(project, key)`
  — an empty `project` addresses the standalone notes.

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
dependencies beyond the MCP SDK. The storage root is resolved once at startup
from `AGENT_MEMORY_DIR` (default `~/.agent-memory/`).
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
| --- | --- | --- |
| `AGENT_MEMORY_DIR` | `~/.agent-memory` | Storage root — point to an iCloud/Dropbox folder to sync across machines |
| `AGENT_MEMORY_MAX_DEPTH` | `5` | Maximum number of segments in a project path |

Set it in the MCPGateway entry for this server, e.g.:

```json
{"env": {"AGENT_MEMORY_DIR": "/Users/you/Library/Mobile Documents/com~apple~CloudDocs/agent-memory"}}
```

## Conventions

- Python 3.14+, managed with `uv`
- `structlog` for logging
- `ruff` for linting and formatting
- No sandboxing — the server runs with full filesystem access under the user
  account, consistent with MCPGateway and lmstudio-code-cli
- Follows the same repo structure as other tools in this tools repository
