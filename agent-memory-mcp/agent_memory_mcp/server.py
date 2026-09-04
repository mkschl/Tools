"""Agent-memory MCP server — shared persistent memory for MCPGateway tools."""

import structlog
from mcp.server.fastmcp import FastMCP

from agent_memory_mcp import storage

log = structlog.get_logger()

mcp = FastMCP("agent-memory")


@mcp.tool()
def decision_log(project: str, summary: str, reasoning: str, create: bool = False) -> str:
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
    result = storage.decision_log(project, summary, reasoning, create)
    log.info("decision_log", project=project, summary=summary)
    return result


@mcp.tool()
def decision_read(project: str = "", days: int = 7) -> str:
    """Return recent decision log entries.

    Args:
        project: Project name. Omit or pass empty string to read all projects.
        days: How many days back to look (default 7).
    """
    result = storage.decision_read(project or None, days)
    log.info("decision_read", project=project or "*", days=days)
    return result


@mcp.tool()
def kanban_create(project: str, columns: str) -> str:
    """Create a new kanban board with the given columns.

    Args:
        project: Project name (used as the board file name).
        columns: Comma-separated list of column names, e.g. 'To Do, In Progress, Done'.
    """
    column_list = [c.strip() for c in columns.split(",") if c.strip()]
    result = storage.kanban_create(project, column_list)
    log.info("kanban_create", project=project, columns=column_list)
    return result


@mcp.tool()
def kanban_add_column(project: str, column: str, after: str = "") -> str:
    """Add a new empty column to a kanban board, or move an existing one.

    Args:
        project: Project name (used as the board file name).
        column: Name of the column to add (or move).
        after: Optional name of the column to insert after. If omitted, appends to the end.
               If the column already exists, it is moved to this position.
    """
    result = storage.kanban_add_column(project, column, after)
    log.info("kanban_add_column", project=project, column=column, after=after)
    return result


@mcp.tool()
def kanban_add(
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
    result = storage.kanban_add(
        project, column, title, description, due, tag_list, priority, step_list, note_list
    )
    log.info("kanban_add", project=project, column=column, title=title)
    return result


@mcp.tool()
def kanban_move(project: str, title: str, to_column: str) -> str:
    """Move a card to a different column.

    Args:
        project: Project name.
        title: Card title to move (case-insensitive match).
        to_column: Destination column name (case-insensitive).
    """
    result = storage.kanban_move(project, title, to_column)
    log.info("kanban_move", project=project, title=title, to_column=to_column)
    return result


@mcp.tool()
def kanban_delete_card(project: str, title: str) -> str:
    """Delete a card from the board.

    Args:
        project: Project name.
        title: Card title to delete (case-insensitive match).
    """
    result = storage.kanban_delete_card(project, title)
    log.info("kanban_delete_card", project=project, title=title)
    return result


@mcp.tool()
def kanban_delete_column(project: str, column: str) -> str:
    """Delete an empty column from the board. Fails if the column still has cards.

    Args:
        project: Project name.
        column: Column name to delete (case-insensitive match).
    """
    result = storage.kanban_delete_column(project, column)
    log.info("kanban_delete_column", project=project, column=column)
    return result


@mcp.tool()
def kanban_update_card(
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
    result = storage.kanban_update_card(
        project,
        title,
        new_title,
        description or None,
        due or None,
        tag_list,
        priority or None,
        step_list,
        note_list,
    )
    log.info("kanban_update_card", project=project, title=title)
    return result


@mcp.tool()
def kanban_list() -> str:
    """List all kanban boards."""
    result = storage.kanban_list()
    log.info("kanban_list")
    return result


@mcp.tool()
def kanban_read(project: str) -> str:
    """Return the full kanban board for a project.

    Args:
        project: Project name.
    """
    result = storage.kanban_read(project)
    log.info("kanban_read", project=project)
    return result


@mcp.tool()
def kanban_delete(project: str) -> str:
    """Delete an entire kanban board.

    Args:
        project: Project name of the board to delete.
    """
    result = storage.kanban_delete(project)
    log.info("kanban_delete", project=project)
    return result


@mcp.tool()
def kanban_rename(project: str, new_name: str) -> str:
    """Rename a kanban board.

    Args:
        project: Current project name.
        new_name: New project name.
    """
    result = storage.kanban_rename(project, new_name)
    log.info("kanban_rename", project=project, new_name=new_name)
    return result


@mcp.tool()
def kanban_search(query: str, project: str = "") -> str:
    """Search for cards matching a keyword across all boards (or a single board).

    Matches against card titles, descriptions, and tags.

    Args:
        query: Keyword to search for (case-insensitive).
        project: Optional project name to limit the search to one board.
    """
    result = storage.kanban_search(query, project)
    log.info("kanban_search", query=query, project=project or "*")
    return result


@mcp.tool()
def note_write(project: str, key: str, content: str, create: bool = False) -> str:
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
    result = storage.note_write(project, key, content, create)
    log.info("note_write", project=project, key=key)
    return result


@mcp.tool()
def note_read(project: str, key: str) -> str:
    """Read a note for a project.

    Args:
        project: Project name.
        key: Note identifier.
    """
    result = storage.note_read(project, key)
    log.info("note_read", project=project, key=key)
    return result


@mcp.tool()
def note_list(project: str) -> str:
    """List all note keys for a project.

    Args:
        project: Project name.
    """
    result = storage.note_list(project)
    log.info("note_list", project=project)
    return result


@mcp.tool()
def note_delete(project: str, key: str) -> str:
    """Delete a note for a project.

    Args:
        project: Project name.
        key: Note identifier to delete.
    """
    result = storage.note_delete(project, key)
    log.info("note_delete", project=project, key=key)
    return result


@mcp.tool()
def project_list() -> str:
    """List every project across boards, notes, and decisions, with counts.

    Call this before writing to a project name you're not certain already
    exists — it groups near-duplicate spellings (e.g. 'JobSearch' vs
    'job-search') together and flags them as collisions, so a typo or casing
    slip doesn't silently create a sibling project instead of finding the
    existing one.
    """
    result = storage.project_list()
    log.info("project_list")
    return result


@mcp.tool()
def project_summary(project: str, days: int = 7) -> str:
    """Return a full project summary: kanban board, recent decisions, and notes.

    Args:
        project: Project name.
        days: How many days back to include decisions (default 7).
    """
    result = storage.project_summary(project, days)
    log.info("project_summary", project=project, days=days)
    return result


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    log.info("starting", storage=str(storage.STORAGE_ROOT))
    mcp.run()


if __name__ == "__main__":
    main()
