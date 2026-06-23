"""Agent-memory MCP server — shared persistent memory for MCPGateway tools."""

import structlog
from mcp.server.fastmcp import FastMCP

from agent_memory_mcp import storage

log = structlog.get_logger()

mcp = FastMCP("agent-memory")


@mcp.tool()
def decision_log(project: str, summary: str, reasoning: str) -> str:
    """Append a decision entry to the project's dated decision log.

    Args:
        project: Project name (used as directory name, e.g. 'myapp').
        summary: Short one-line description of the decision.
        reasoning: Full explanation of why this decision was made.
    """
    result = storage.decision_log(project, summary, reasoning)
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
def kanban_add(
    project: str,
    column: str,
    title: str,
    description: str = "",
    due: str = "",
    tags: str = "",
    priority: str = "",
    steps: str = "",
) -> str:
    """Add a card to a kanban column. Creates the board with default columns if it does not exist.

    Default columns are: Backlog, In Progress, Done.

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
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    step_list = [s.strip() for s in steps.splitlines() if s.strip()] if steps else []
    result = storage.kanban_add(
        project, column, title, description, due, tag_list, priority, step_list
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
def kanban_read(project: str) -> str:
    """Return the full kanban board for a project.

    Args:
        project: Project name.
    """
    result = storage.kanban_read(project)
    log.info("kanban_read", project=project)
    return result


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    log.info("starting", storage=str(storage.STORAGE_ROOT))
    mcp.run()


if __name__ == "__main__":
    main()
