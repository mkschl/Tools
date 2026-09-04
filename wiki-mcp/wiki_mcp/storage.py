"""File-based access to a markdown wiki repo, rooted at WIKI_DIR."""

import difflib
import os
import re
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

from wiki_mcp import markdownlint

_EXCLUDED_DIR_NAMES = {"build", "dist", "node_modules", "__pycache__"}


def _check_wiki_dir(path: Path) -> None:
    if not path.is_dir():
        raise RuntimeError(f"WIKI_DIR={str(path)!r} does not exist or is not a directory.")
    probe = path / ".wiki-mcp-write-check"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(f"WIKI_DIR={str(path)!r} is not writable: {exc}") from exc


_env_dir = os.environ.get("WIKI_DIR")
if not _env_dir:
    raise RuntimeError(
        "WIKI_DIR environment variable must be set to the wiki repo's root directory."
    )
WIKI_ROOT = Path(_env_dir).expanduser()
_check_wiki_dir(WIKI_ROOT)


# ── Path resolution & traversal safety ──────────────────────────────────────


def _is_excluded_dir(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in _EXCLUDED_DIR_NAMES:
        return True
    return name.endswith(".egg-info")


def _iter_markdown_files(base: Path | None = None) -> Iterator[Path]:
    root = base or WIKI_ROOT
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]
        for name in filenames:
            if name.endswith(".md"):
                yield Path(dirpath) / name


def _resolve_page_path(page: str) -> Path:
    """Resolve a wiki-relative page path, refusing one that would escape WIKI_ROOT."""
    root = WIKI_ROOT.resolve()
    candidate = (root / page).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"Page path '{page}' escapes the wiki root.")
    return candidate


def _all_page_paths() -> list[str]:
    root = WIKI_ROOT.resolve()
    return sorted(str(p.relative_to(root)) for p in _iter_markdown_files())


def _suggest_page(page: str) -> str | None:
    match = difflib.get_close_matches(page, _all_page_paths(), n=1, cutoff=0.55)
    return match[0] if match else None


# ── Reading ──────────────────────────────────────────────────────────────────


def list_pages(directory: str = "") -> str:
    try:
        base = _resolve_page_path(directory) if directory else WIKI_ROOT.resolve()
    except ValueError as exc:
        return str(exc)
    if not base.is_dir():
        return f"Directory '{directory}' not found."
    root = WIKI_ROOT.resolve()
    pages = sorted(str(p.relative_to(root)) for p in _iter_markdown_files(base))
    if not pages:
        return f"No pages found under '{directory or '.'}'."
    return "\n".join(pages)


def read_page(page: str) -> str:
    try:
        path = _resolve_page_path(page)
    except ValueError as exc:
        return str(exc)
    if not path.is_file():
        msg = f"Page '{page}' not found."
        suggestion = _suggest_page(page)
        if suggestion:
            msg += f" Did you mean '{suggestion}'?"
        return msg
    return path.read_text(encoding="utf-8")


def search(query: str, limit: int = 100) -> str:
    query_lower = query.lower()
    root = WIKI_ROOT.resolve()
    results = []
    for md_file in _iter_markdown_files():
        try:
            lines = md_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = md_file.relative_to(root)
        for i, line in enumerate(lines, start=1):
            if query_lower in line.lower():
                results.append(f"{rel}:{i}: {line.strip()}")
                if len(results) >= limit:
                    return "\n".join(results) + f"\n… truncated at {limit} matches"
    if not results:
        return f"No matches for '{query}'."
    return "\n".join(results)


# ── Links ────────────────────────────────────────────────────────────────────

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _extract_md_link_targets(md_path: Path) -> list[Path]:
    """Return resolved paths this page links to, for markdown links to other .md pages."""
    try:
        content = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    targets = []
    for match in _LINK_RE.finditer(content):
        raw = match.group(1).strip()
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        raw = raw.split(" ", 1)[0]  # drop a trailing "title" in (url "title")
        raw = raw.split("#", 1)[0]  # drop an anchor fragment
        if not raw.lower().endswith(".md"):
            continue
        decoded = urllib.parse.unquote(raw)
        try:
            resolved = (md_path.parent / decoded).resolve()
        except (OSError, ValueError):
            continue
        targets.append(resolved)
    return targets


def backlinks(page: str) -> str:
    try:
        target = _resolve_page_path(page)
    except ValueError as exc:
        return str(exc)
    if not target.is_file():
        msg = f"Page '{page}' not found."
        suggestion = _suggest_page(page)
        if suggestion:
            msg += f" Did you mean '{suggestion}'?"
        return msg
    root = WIKI_ROOT.resolve()
    target_key = str(target).lower()
    matches = []
    for md_file in _iter_markdown_files():
        resolved = md_file.resolve()
        if resolved == target:
            continue
        for link_target in _extract_md_link_targets(md_file):
            # Case-insensitive compare: this wiki has pre-existing links whose
            # casing doesn't exactly match their target filename, which only
            # "work" today because macOS's filesystem is case-insensitive.
            if str(link_target).lower() == target_key:
                matches.append(str(resolved.relative_to(root)))
                break
    if not matches:
        return f"No pages link to '{page}'."
    return "\n".join(sorted(matches))


# ── Writing ──────────────────────────────────────────────────────────────────


def write_page(page: str, content: str, create: bool = False) -> str:
    if not page.endswith(".md"):
        return f"Page path '{page}' must end in .md."
    try:
        path = _resolve_page_path(page)
    except ValueError as exc:
        return str(exc)
    existed_before = path.is_file()
    if not existed_before and not create:
        msg = f"Page '{page}' does not exist."
        suggestion = _suggest_page(page)
        if suggestion:
            msg += f" Did you mean '{suggestion}'?"
        msg += " Pass create=true to create a new page."
        return msg

    fixed_content, remaining_issues = markdownlint.fix_content(content, WIKI_ROOT.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fixed_content, encoding="utf-8")

    action = "Updated" if existed_before else "Created"
    result = f"{action} page '{page}'."
    if remaining_issues:
        result += f"\n\nmarkdownlint issues:\n{markdownlint.format_issues(remaining_issues)}"
    return result


def delete_page(page: str, confirm: bool = False) -> str:
    try:
        path = _resolve_page_path(page)
    except ValueError as exc:
        return str(exc)
    if not path.is_file():
        return f"Page '{page}' not found."
    if not confirm:
        inbound = backlinks(page)
        msg = f"Pass confirm=true to delete '{page}'."
        if not inbound.startswith("No pages link"):
            linking_pages = inbound.splitlines()
            msg = (
                f"Page '{page}' is linked from {len(linking_pages)} page(s): "
                f"{linking_pages}. Those links will break. " + msg
            )
        return msg
    path.unlink()
    return f"Deleted page '{page}'."
