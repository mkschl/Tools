"""Agent-memory MCP server — shared persistent memory for MCPGateway tools."""

import asyncio
import contextlib
from collections.abc import Callable

import anyio.to_thread
import structlog
from mcp.server.fastmcp import FastMCP

from agent_memory_mcp import storage

log = structlog.get_logger()

mcp = FastMCP("agent-memory")

# AGENT_MEMORY_DIR is commonly pointed at an iCloud/Dropbox-synced folder (to
# sync across machines) — a plain file read/write there can stall for minutes
# if the sync client needs to fetch an evicted local copy or is otherwise
# slow. FastMCP calls a synchronous tool function directly on its single
# asyncio event loop with no thread-pool offloading, so without _run, one
# stalled call freezes the *entire* server — including unrelated reads
# queued behind it. This was observed as "reads fine, writes hanging, and a
# subsequent read blocked behind the hung write" — there was no lock anywhere
# in this codebase; a blocked event loop produces the identical symptom.
_SLOW_OPERATION_TIMEOUT_SECONDS = 25.0

# storage.py's kanban/note/decision functions all do a non-atomic
# read-modify-write against one project's files (e.g. kanban_update_card:
# read the board, mutate it in memory, write it back). Before _run existed,
# FastMCP's single event loop made this safe *by accident* — two tool calls
# could never actually run at the same time. Offloading each call to its own
# worker thread removes that accidental serialization, so two concurrent
# calls touching the same project can now race: both read the same starting
# state, both write back, and the second write silently clobbers the first
# (reproduced directly: two concurrent kanban_add calls to one board, one
# card vanished with no error to either caller). A lock per normalized
# project slug — the same normalization storage.py itself uses to resolve a
# project name, so 'JobSearch' and 'job-search' correctly share one lock —
# restores that serialization for calls touching the same project, while
# calls touching different projects still run fully concurrently.
_project_locks: dict[str, asyncio.Lock] = {}


def _locks_for(projects: str | tuple[str, ...] | None) -> list[asyncio.Lock]:
    """Resolve one or more project names to their locks, deduplicated by
    normalized slug and returned in a globally-consistent sorted order.

    The sorted order matters whenever more than one lock is requested (e.g.
    kanban_rename locking both its source and destination name): two
    operations that each need two of the same locks could otherwise acquire
    them in opposite orders and deadlock. Since *every* multi-lock caller
    goes through this same sort, that can't happen — whichever locks a call
    needs, it always acquires them in the same relative order as any other
    call that also needs them.
    """
    if not projects:
        return []
    names = (projects,) if isinstance(projects, str) else projects
    slugs = sorted({storage._slugify(p) for p in names if p})
    return [_project_locks.setdefault(slug, asyncio.Lock()) for slug in slugs]


async def _call_in_thread(
    func: Callable[..., str], args: tuple[object, ...], locks: list[asyncio.Lock]
) -> str:
    async with contextlib.AsyncExitStack() as stack:
        for lock in locks:
            await stack.enter_async_context(lock)
        # abandon_on_cancel=True only matters for a genuine external
        # cancellation of this task (e.g. process shutdown cancelling every
        # outstanding asyncio task) — _run's own timeout can never trigger it,
        # since asyncio.shield() stops that cancellation from ever reaching
        # here in the first place. On a real shutdown, releasing a lock
        # early by abandoning the thread is harmless: the whole process
        # (and _project_locks with it) is going away, so there's no future
        # caller left in this process that could exploit the early release.
        return await anyio.to_thread.run_sync(func, *args, abandon_on_cancel=True)


def _log_late_completion(task: asyncio.Task[str]) -> None:
    """Attached only to a task whose caller already gave up waiting on it
    (see the TimeoutError branch in _run) — makes sure its eventual outcome
    doesn't vanish silently. Without this, a late failure is just an
    "Task exception was never retrieved" warning with no context, and a late
    success isn't visible anywhere at all."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("delayed_operation_failed", error=str(exc))
    else:
        log.info("delayed_operation_completed")


async def _run(
    func: Callable[..., str], *args: object, project: str | tuple[str, ...] | None = None
) -> str:
    """Run a blocking storage call in a worker thread, with a timeout.

    Offloading to a worker thread keeps a stall confined to this one request
    instead of freezing the server for everyone. `project`, when given,
    serializes this call against every other call touching the same
    normalized project(s) — see the module-level comment above for why
    that's necessary. Pass a tuple when a call touches more than one project
    (e.g. kanban_rename's source and destination names both need protecting,
    or two concurrent renames into the same destination would race with
    neither side locked).

    The timeout means a genuinely stuck call still returns promptly with an
    informative message instead of hanging silently. Unlike a bare
    `anyio.move_on_after`, the underlying task is *not* cancelled on timeout
    (it's wrapped in `asyncio.shield`) — it keeps running, and keeps holding
    `project`'s lock(s), until it actually finishes. Cancelling it on timeout
    would release the lock early and let a subsequent call for the same
    project start a new read-modify-write cycle while the first one's write
    is still in flight, reintroducing the exact race this lock exists to
    prevent.
    """
    task = asyncio.create_task(_call_in_thread(func, args, _locks_for(project)))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_SLOW_OPERATION_TIMEOUT_SECONDS)
    except TimeoutError:
        task.add_done_callback(_log_late_completion)
        return (
            f"Still working after {_SLOW_OPERATION_TIMEOUT_SECONDS:.0f}s — this usually means "
            "the storage folder (iCloud/Dropbox-synced) is slow to respond right now. The "
            "operation is continuing in the background; check again shortly."
        )


@mcp.tool()
async def decision_log(project: str, summary: str, reasoning: str, create: bool = False) -> str:
    """Append a decision entry to the project's dated decision log.

    Project names are normalized (casing/separators ignored) so 'JobSearch' and
    'job-search' resolve to the same project. Fails on an unknown project name
    unless create=true — call project_list first if unsure whether it exists.

    Args:
        project: Project name (used as directory name, e.g. 'myapp').
        summary: Short one-line description of the decision.
        reasoning: Full explanation of why this decision was made.
        create: Pass true to create a new project if it doesn't exist yet.
    """
    result = await _run(storage.decision_log, project, summary, reasoning, create, project=project)
    log.info("decision_log", project=project, summary=summary)
    return result


@mcp.tool()
async def decision_read(project: str = "", days: int = 7) -> str:
    """Return recent decision log entries.

    Args:
        project: Project name. Omit or pass empty string to read all projects.
        days: How many days back to look (default 7).
    """
    # No project lock: read-only, and storage.py's writes are atomic (see
    # storage._atomic_write_text), so this can never observe a half-written
    # file — only see the complete old or complete new content. Not locking
    # reads means a wedged write for one project can never block reads for
    # that same project forever; only other writes to it wait.
    result = await _run(storage.decision_read, project or None, days)
    log.info("decision_read", project=project or "*", days=days)
    return result


@mcp.tool()
async def kanban_create(project: str, columns: str) -> str:
    """Create a new kanban board with the given columns.

    Args:
        project: Project name (used as the board file name).
        columns: Comma-separated list of column names, e.g. 'To Do, In Progress, Done'.
    """
    column_list = [c.strip() for c in columns.split(",") if c.strip()]
    result = await _run(storage.kanban_create, project, column_list, project=project)
    log.info("kanban_create", project=project, columns=column_list)
    return result


@mcp.tool()
async def kanban_add_column(project: str, column: str, after: str = "") -> str:
    """Add a new empty column to a kanban board, or move an existing one.

    Args:
        project: Project name (used as the board file name).
        column: Name of the column to add (or move).
        after: Optional name of the column to insert after. If omitted, appends to the end.
               If the column already exists, it is moved to this position.
    """
    result = await _run(storage.kanban_add_column, project, column, after, project=project)
    log.info("kanban_add_column", project=project, column=column, after=after)
    return result


@mcp.tool()
async def kanban_add(
    project: str,
    column: str,
    title: str,
    description: str = "",
    due: str = "",
    tags: str = "",
    priority: str = "",
    steps: str = "",
    notes: str = "",
) -> str:
    """Add a card to a kanban column.

    Args:
        project: Project name (used as the board file name).
        column: Column to add the card to (case-insensitive).
        title: Card title.
        description: Optional body text for the card.
        due: Optional due date, e.g. '2024-01-15'.
        tags: Optional comma-separated tags, e.g. 'design, backend'.
        priority: Optional priority: high, medium, or low.
        steps: Optional newline-separated checklist items. Prefix with '[x] ' for done
            or '[ ] ' for pending, e.g. '[x] Research\n[ ] Implement\n[ ] Review'.
        notes: Optional comma-separated note keys already written for this project
            (via note_write). Validated against note_list — fails if any key is missing.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    step_list = [s.strip() for s in steps.splitlines() if s.strip()] if steps else []
    note_list = [n.strip() for n in notes.split(",") if n.strip()] if notes else []
    result = await _run(
        storage.kanban_add,
        project,
        column,
        title,
        description,
        due,
        tag_list,
        priority,
        step_list,
        note_list,
        project=project,
    )
    log.info("kanban_add", project=project, column=column, title=title)
    return result


@mcp.tool()
async def kanban_move(project: str, title: str, to_column: str) -> str:
    """Move a card to a different column.

    Args:
        project: Project name.
        title: Card title to move (case-insensitive match).
        to_column: Destination column name (case-insensitive).
    """
    result = await _run(storage.kanban_move, project, title, to_column, project=project)
    log.info("kanban_move", project=project, title=title, to_column=to_column)
    return result


@mcp.tool()
async def kanban_delete_card(project: str, title: str) -> str:
    """Delete a card from the board.

    Args:
        project: Project name.
        title: Card title to delete (case-insensitive match).
    """
    result = await _run(storage.kanban_delete_card, project, title, project=project)
    log.info("kanban_delete_card", project=project, title=title)
    return result


@mcp.tool()
async def kanban_delete_column(project: str, column: str) -> str:
    """Delete an empty column from the board. Fails if the column still has cards.

    Args:
        project: Project name.
        column: Column name to delete (case-insensitive match).
    """
    result = await _run(storage.kanban_delete_column, project, column, project=project)
    log.info("kanban_delete_column", project=project, column=column)
    return result


@mcp.tool()
async def kanban_update_card(
    project: str,
    title: str,
    new_title: str = "",
    description: str = "",
    due: str = "",
    tags: str = "",
    priority: str = "",
    steps: str = "",
    notes: str = "",
) -> str:
    """Update fields on an existing card. Only supplied fields are changed.
    Pass an empty string to clear a field (due, tags, priority, steps, notes, description).

    Args:
        project: Project name.
        title: Card title to update (case-insensitive match).
        new_title: Rename the card.
        description: New body text.
        due: New due date, e.g. '2024-01-15'. Empty string clears it.
        tags: Comma-separated tags, e.g. 'design, backend'. Empty string clears them.
        priority: New priority: high, medium, or low. Empty string clears it.
        steps: Newline-separated checklist items. Empty string clears them.
        notes: Comma-separated note keys already written for this project (via
            note_write). Validated against note_list. Empty string clears them.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    step_list = [s.strip() for s in steps.splitlines() if s.strip()] if steps else None
    note_list = [n.strip() for n in notes.split(",") if n.strip()] if notes else None
    result = await _run(
        storage.kanban_update_card,
        project,
        title,
        new_title,
        description or None,
        due or None,
        tag_list,
        priority or None,
        step_list,
        note_list,
        project=project,
    )
    log.info("kanban_update_card", project=project, title=title)
    return result


@mcp.tool()
async def kanban_list() -> str:
    """List all kanban boards."""
    result = await _run(storage.kanban_list)
    log.info("kanban_list")
    return result


@mcp.tool()
async def kanban_read(project: str) -> str:
    """Return the full kanban board for a project.

    Args:
        project: Project name.
    """
    # No project lock — see decision_read's comment above.
    result = await _run(storage.kanban_read, project)
    log.info("kanban_read", project=project)
    return result


@mcp.tool()
async def kanban_delete(project: str) -> str:
    """Delete an entire kanban board.

    Args:
        project: Project name of the board to delete.
    """
    result = await _run(storage.kanban_delete, project, project=project)
    log.info("kanban_delete", project=project)
    return result


@mcp.tool()
async def kanban_rename(project: str, new_name: str) -> str:
    """Rename a kanban board.

    Args:
        project: Current project name.
        new_name: New project name.
    """
    # Locks both names: a concurrent write to `new_name`, or a second
    # concurrent rename targeting the same `new_name` from a different
    # source, would otherwise race completely unprotected at the
    # destination (neither side's source-only lock covers it).
    result = await _run(storage.kanban_rename, project, new_name, project=(project, new_name))
    log.info("kanban_rename", project=project, new_name=new_name)
    return result


@mcp.tool()
async def kanban_search(query: str, project: str = "") -> str:
    """Search for cards matching a keyword across all boards (or a single board).

    Matches against card titles, descriptions, and tags.

    Args:
        query: Keyword to search for (case-insensitive).
        project: Optional project name to limit the search to one board.
    """
    # No project lock — see decision_read's comment above.
    result = await _run(storage.kanban_search, query, project)
    log.info("kanban_search", query=query, project=project or "*")
    return result


@mcp.tool()
async def note_write(project: str, key: str, content: str, create: bool = False) -> str:
    """Write (create or overwrite) a note for a project.

    Project names are normalized (casing/separators ignored) so 'JobSearch' and
    'job-search' resolve to the same project. Fails on an unknown project name
    unless create=true — call project_list first if unsure whether it exists.

    Args:
        project: Project name.
        key: Note identifier, e.g. 'architecture', 'env-setup', 'api-endpoints'.
        content: Full text content of the note.
        create: Pass true to create a new project if it doesn't exist yet.
    """
    result = await _run(storage.note_write, project, key, content, create, project=project)
    log.info("note_write", project=project, key=key)
    return result


@mcp.tool()
async def note_read(project: str, key: str) -> str:
    """Read a note for a project.

    Args:
        project: Project name.
        key: Note identifier.
    """
    # No project lock — see decision_read's comment above.
    result = await _run(storage.note_read, project, key)
    log.info("note_read", project=project, key=key)
    return result


@mcp.tool()
async def note_list(project: str) -> str:
    """List all note keys for a project.

    Args:
        project: Project name.
    """
    # No project lock — see decision_read's comment above.
    result = await _run(storage.note_list, project)
    log.info("note_list", project=project)
    return result


@mcp.tool()
async def note_delete(project: str, key: str) -> str:
    """Delete a note for a project.

    Args:
        project: Project name.
        key: Note identifier to delete.
    """
    result = await _run(storage.note_delete, project, key, project=project)
    log.info("note_delete", project=project, key=key)
    return result


@mcp.tool()
async def project_list() -> str:
    """List every project across boards, notes, and decisions, with counts.

    Call this before writing to a project name you're not certain already
    exists — it groups near-duplicate spellings (e.g. 'JobSearch' vs
    'job-search') together and flags them as collisions, so a typo or casing
    slip doesn't silently create a sibling project instead of finding the
    existing one.
    """
    result = await _run(storage.project_list)
    log.info("project_list")
    return result


@mcp.tool()
async def project_summary(project: str, days: int = 7) -> str:
    """Return a full project summary: kanban board, recent decisions, and notes.

    This is the documented default way to open a project — it returns the
    board, decisions, and notes together in one call, rather than needing
    three separate lookups.

    Args:
        project: Project name.
        days: How many days back to include decisions (default 7).
    """
    # No project lock — see decision_read's comment above.
    result = await _run(storage.project_summary, project, days)
    log.info("project_summary", project=project, days=days)
    return result


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    log.info("starting", storage=str(storage.STORAGE_ROOT))
    mcp.run()


if __name__ == "__main__":
    main()
