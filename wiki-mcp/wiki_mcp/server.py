"""wiki-mcp server — read/search/write access to a markdown wiki repo."""

import structlog
from mcp.server.fastmcp import FastMCP

from wiki_mcp import storage

log = structlog.get_logger()

mcp = FastMCP("wiki")


@mcp.tool()
def list_pages(directory: str = "") -> str:
    """List all wiki pages (.md files), optionally scoped to a subdirectory.

    Args:
        directory: Optional subdirectory path relative to the wiki root, e.g.
            'Containerisation/Helm'. Omit to list every page in the wiki.
    """
    result = storage.list_pages(directory)
    log.info("list_pages", directory=directory or "*")
    return result


@mcp.tool()
def read_page(page: str) -> str:
    """Read a wiki page's raw markdown content.

    Args:
        page: Page path relative to the wiki root, e.g.
            'Containerisation/Helm/Chart Template Guide/Values Files.md'.
    """
    result = storage.read_page(page)
    log.info("read_page", page=page)
    return result


@mcp.tool()
def search(query: str, limit: int = 100) -> str:
    """Search wiki page contents for a keyword (case-insensitive substring match).

    Args:
        query: Text to search for.
        limit: Maximum number of matching lines to return (default 100).
    """
    result = storage.search(query, limit)
    log.info("search", query=query)
    return result


@mcp.tool()
def backlinks(page: str) -> str:
    """List every wiki page that links to the given page.

    Resolves markdown links (`[text](relative/path.md)`), not just exact
    string matches, so it follows relative paths and URL-encoded spaces.

    Args:
        page: Page path relative to the wiki root.
    """
    result = storage.backlinks(page)
    log.info("backlinks", page=page)
    return result


@mcp.tool()
def write_page(page: str, content: str, create: bool = False) -> str:
    """Write (overwrite) a wiki page's content.

    Fails on a page that doesn't exist yet unless create=true, so a typo'd
    path doesn't silently create an unintended new page. Runs the content
    through markdownlint --fix before saving, same as the rest of this wiki.

    Args:
        page: Page path relative to the wiki root, e.g. 'Cloud/AWS/Lambda.md'.
        content: Full markdown content for the page.
        create: Pass true to create a new page at this path.
    """
    result = storage.write_page(page, content, create)
    log.info("write_page", page=page, create=create)
    return result


@mcp.tool()
def delete_page(page: str, confirm: bool = False) -> str:
    """Delete a wiki page.

    Reports any pages that link to it and requires confirm=true before
    actually deleting, since removing a linked-to page leaves broken links
    behind with no automatic fixup.

    Args:
        page: Page path relative to the wiki root.
        confirm: Pass true to actually delete. Without it, this only reports
            what would happen.
    """
    result = storage.delete_page(page, confirm)
    log.info("delete_page", page=page, confirm=confirm)
    return result


def main() -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    log.info("starting", wiki_root=str(storage.WIKI_ROOT))
    mcp.run()


if __name__ == "__main__":
    main()
