"""wiki-mcp server — read/search/write access to a markdown wiki repo."""

from collections.abc import Callable

import anyio.to_thread
import structlog
from mcp.server.fastmcp import FastMCP

from wiki_mcp import storage

log = structlog.get_logger()

mcp = FastMCP("wiki")

# WIKI_DIR can be pointed at a synced folder the same way agent-memory-mcp's
# AGENT_MEMORY_DIR commonly is — a plain file read/write there can stall for
# minutes if the sync client needs to fetch an evicted local copy or is
# otherwise slow. FastMCP calls a synchronous @mcp.tool() function directly
# on its single asyncio event loop with no thread-pool offloading, so
# without _run, one stalled call would freeze the *entire* server for every
# concurrent request — the same bug agent-memory-mcp had and fixed; see that
# server's CLAUDE.md for how it was found (a 4-minute hung write with a
# concurrent read also blocked behind it).
_SLOW_OPERATION_TIMEOUT_SECONDS = 25.0


async def _run(func: Callable[..., str], *args: object) -> str:
    """Run a blocking storage call in a worker thread, with a timeout.

    Unlike agent-memory-mcp's equivalent helper, this takes no per-resource
    lock: write_page fully replaces a page's content from the caller's own
    argument rather than reading-modifying-writing existing state, so two
    concurrent writes to the same page are an ordinary last-write-wins, not
    a lost update. What still matters here is exactly what motivated the
    lock there in the first place — every write goes through
    storage._atomic_write_text, so a concurrent lock-free read can only ever
    see the complete old file or the complete new one.
    """
    with anyio.move_on_after(_SLOW_OPERATION_TIMEOUT_SECONDS):
        return await anyio.to_thread.run_sync(func, *args, abandon_on_cancel=True)
    return (
        f"Still working after {_SLOW_OPERATION_TIMEOUT_SECONDS:.0f}s — this usually means "
        "the wiki folder is slow to respond right now. The operation is continuing in the "
        "background; check again shortly."
    )


@mcp.tool()
async def list_pages(directory: str = "") -> str:
    """List all wiki pages (.md files), optionally scoped to a subdirectory.

    Args:
        directory: Optional subdirectory path relative to the wiki root, e.g.
            'Containerisation/Helm'. Omit to list every page in the wiki.
    """
    result = await _run(storage.list_pages, directory)
    log.info("list_pages", directory=directory or "*")
    return result


@mcp.tool()
async def read_page(page: str) -> str:
    """Read a wiki page's raw markdown content.

    Args:
        page: Page path relative to the wiki root, e.g.
            'Containerisation/Helm/Chart Template Guide/Values Files.md'.
    """
    result = await _run(storage.read_page, page)
    log.info("read_page", page=page)
    return result


@mcp.tool()
async def search(query: str, limit: int = 100) -> str:
    """Search wiki page contents for a keyword (case-insensitive substring match).

    Args:
        query: Text to search for.
        limit: Maximum number of matching lines to return (default 100).
    """
    result = await _run(storage.search, query, limit)
    log.info("search", query=query)
    return result


@mcp.tool()
async def backlinks(page: str) -> str:
    """List every wiki page that links to the given page.

    Resolves markdown links (`[text](relative/path.md)`), not just exact
    string matches, so it follows relative paths and URL-encoded spaces.

    Args:
        page: Page path relative to the wiki root.
    """
    result = await _run(storage.backlinks, page)
    log.info("backlinks", page=page)
    return result


@mcp.tool()
async def write_page(page: str, content: str, create: bool = False) -> str:
    """Write (overwrite) a wiki page's content.

    Fails on a page that doesn't exist yet unless create=true, so a typo'd
    path doesn't silently create an unintended new page. Runs the content
    through markdownlint --fix before saving, same as the rest of this wiki.

    Args:
        page: Page path relative to the wiki root, e.g. 'Cloud/AWS/Lambda.md'.
        content: Full markdown content for the page.
        create: Pass true to create a new page at this path.
    """
    result = await _run(storage.write_page, page, content, create)
    log.info("write_page", page=page, create=create)
    return result


@mcp.tool()
async def delete_page(page: str, confirm: bool = False) -> str:
    """Delete a wiki page.

    Reports any pages that link to it and requires confirm=true before
    actually deleting, since removing a linked-to page leaves broken links
    behind with no automatic fixup.

    Args:
        page: Page path relative to the wiki root.
        confirm: Pass true to actually delete. Without it, this only reports
            what would happen.
    """
    result = await _run(storage.delete_page, page, confirm)
    log.info("delete_page", page=page, confirm=confirm)
    return result


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    log.info("starting", wiki_root=str(storage.WIKI_ROOT))
    mcp.run()


if __name__ == "__main__":
    main()
