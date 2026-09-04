# CLAUDE.md — wiki-mcp

This file provides guidance to Claude Code when working in this repository.

## Commands

```sh
# Install / sync dependencies
uv sync
uv sync --extra dev   # includes pytest

# Run the MCP server
WIKI_DIR=/path/to/wiki uv run wiki-mcp

# Test
uv run pytest

# Lint
uv run ruff check wiki_mcp/
uv run ruff format wiki_mcp/
```

## Purpose

An MCP server exposing read, search, and write access to an external markdown
wiki repo (a personal knowledge base of nested folders and cross-linked
`.md` pages) — it does not own or version the content, it just reads/writes
the repo pointed to by `WIKI_DIR`. Nothing in this repo is specific to any
particular wiki's content; the path is entirely configurable.

## Storage Model

There's no separate storage format — pages are exactly the `.md` files
already in the target wiki repo, addressed by their path relative to
`WIKI_DIR`, e.g. `Containerisation/Helm/Chart Template Guide/Values Files.md`.

All wiki-relative paths are resolved and checked against `WIKI_DIR` before
any read/write (`_resolve_page_path` in `storage.py`) to reject path
traversal (`../../etc/passwd`-style escapes) — this matters more here than in
a typical single-purpose server, since page paths are arbitrary
caller-supplied strings mapped directly onto the filesystem.

Directories that aren't wiki content are skipped when walking the repo
(`_is_excluded_dir`): anything starting with `.` (`.git`, `.venv`, editor
config dirs), `build/`, `dist/`, `node_modules/`, `__pycache__/`, and anything
ending in `.egg-info` — the target wiki repo in practice also has a
`pyproject.toml` for managing dependencies used by embedded Jupyter
notebooks, which produces exactly this kind of build noise alongside the
actual wiki pages.

## Link resolution (backlinks)

The target wiki uses plain relative markdown links between pages
(`[text](./Other%20Page.md)`, `../parent/Page.md#anchor`), not
Obsidian-style `[[wiki-links]]` — confirmed by inspection, since a `[[...]]`
grep hit only ever matched code (Python list literals, bash `[[ ]]` tests),
never a link. `_extract_md_link_targets` in `storage.py` parses standard
markdown link syntax, URL-decodes the target, drops anchor fragments, and
resolves it relative to the linking page's own directory.

Link comparison is deliberately **case-insensitive**: the wiki has real,
pre-existing links whose casing doesn't match their target filename's actual
casing (e.g. a link to `./built-in%20objects.md` pointing at a file actually
named `Built-In Objects.md`) — these currently "work" only because macOS's
filesystem is case-insensitive. `backlinks` intentionally still counts them,
since from the wiki author's perspective they're valid links, and disagreeing
with what the filesystem itself accepts would make `backlinks` less useful,
not more correct.

## MCP Tools

- `list_pages(directory?)` — list every `.md` page, optionally scoped to a subdirectory.
- `read_page(page)` — read a page's raw markdown. Suggests a close match
  (via `difflib`) if the path doesn't exist.
- `search(query, limit?)` — case-insensitive substring search across all
  page contents, returning `path:line: text` matches (default cap: 100).
- `backlinks(page)` — every page whose markdown links resolve to this page.
- `write_page(page, content, create?)` — write/overwrite a page. Fails on an
  unknown path unless `create=true`, so a typo'd path doesn't silently create
  an unintended new page (same reasoning as `agent-memory-mcp`'s
  `note_write`/`decision_log` gating — see that repo's CLAUDE.md for the
  incident that motivated it). Runs content through `markdownlint --fix`
  first, same as the wiki's own tooling.
- `delete_page(page, confirm?)` — reports inbound backlinks and requires
  `confirm=true` before actually deleting, since removing a linked-to page
  leaves broken links behind with no automatic fixup.

## Markdown linting

`write_page` runs content through the system `markdownlint` binary
(markdownlint-cli, npm) via `wiki_mcp/markdownlint.py`, using the target
wiki's own `<WIKI_DIR>/.markdownlint.json` config (so writes match whatever
rule set the wiki's human-edited pages already follow) — override with
`WIKI_MARKDOWNLINT_CONFIG`. If the binary isn't on `PATH`, linting is skipped
gracefully; writes still succeed, just without the lint pass.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `WIKI_DIR` | *(required, no default)* | Root of the wiki repo to serve. No fallback path — this server has no business embedding any particular person's wiki location, so an unset `WIKI_DIR` fails fast at startup rather than silently defaulting anywhere. |
| `WIKI_MARKDOWNLINT_CONFIG` | `<WIKI_DIR>/.markdownlint.json` | Override the markdownlint config location. |

Set `WIKI_DIR` in the MCPGateway entry for this server, e.g.:

```json
{"env": {"WIKI_DIR": "/Volumes/Dev/Wiki"}}
```

## Architecture

Same shape as `agent-memory-mcp`: MCP stdio transport, `FastMCP`,
`server.py` as thin tool wrappers over `storage.py`'s actual logic, plain
Python type hints on tool parameters, errors returned as strings rather than
raised (except for a genuinely invalid path traversal attempt, which raises
`ValueError` inside `storage.py` and is caught at each call site — an
invalid path is a caller mistake no valid input could produce by accident,
unlike "page doesn't exist yet" which is an expected, recoverable case).

All file I/O is synchronous. No database, no external dependencies beyond
the MCP SDK. No sandboxing — the server runs with full filesystem access
under the user account, scoped to `WIKI_DIR` by the traversal check.

## Conventions

- Python 3.12+, managed with `uv`
- `structlog` for logging
- `ruff` for linting and formatting
- Tests use `importlib.reload(storage)` after `monkeypatch.setenv("WIKI_DIR", ...)`
  per test, since `WIKI_ROOT` is resolved once at import time — see the
  `wiki_root` fixture in `tests/test_storage.py`. `tests/conftest.py` sets a
  throwaway `WIKI_DIR` before collection so the initial module import
  (before any test's `monkeypatch` runs) doesn't fail.
