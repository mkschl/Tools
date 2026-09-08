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

`_extract_md_link_targets` only strips a trailing `(url "title")`/`(url
'title')` annotation when it's actually a quoted title at the very end
(`_LINK_TITLE_RE`), not just "everything after the first space" — an earlier
version did the latter, which truncated a link target that legitimately
contains a literal, non-percent-encoded space (this wiki has plenty, e.g.
`Values Files.md`) at the first space, silently dropping it from `backlinks`
results entirely. `backlinks` and `delete_page`'s confirm-gate share one
`_backlinks_list` helper returning a real `list[str]`, rather than
`delete_page` re-parsing `backlinks`' formatted string output by checking
whether it starts with `"No pages link"` — that string-sniffing broke if
`backlinks` ever returned a different message (e.g. its own "not found" text
for a TOCTOU race — see below), misreporting an error as a real backlink.

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

All file I/O in `storage.py` is synchronous. No database, no external
dependencies beyond the MCP SDK. No sandboxing — the server runs with full
filesystem access under the user account, scoped to `WIKI_DIR` by the
traversal check.

### Why every tool wrapper in server.py is `async` and calls `_run`

`WIKI_DIR` can be pointed at a synced folder the same way
`agent-memory-mcp`'s `AGENT_MEMORY_DIR` commonly is — a plain file
read/write there can stall for minutes if the sync client needs to fetch an
evicted local copy or is otherwise slow. FastMCP calls a synchronous tool
function directly on its single asyncio event loop with **no thread-pool
offloading**, so a single stalled call would freeze the *entire* server —
this is the exact bug `agent-memory-mcp` had and fixed (see that repo's
CLAUDE.md for how it was found: a 4-minute hung write with a concurrent read
blocked behind it).

`_run` in `server.py` offloads each tool's blocking call to a worker thread
via `anyio.to_thread.run_sync`, with the same kind of timeout
(`_SLOW_OPERATION_TIMEOUT_SECONDS`) so a genuinely stuck call returns an
informative message instead of hanging silently — the underlying thread
keeps running to completion in the background regardless.

**Unlike `agent-memory-mcp`'s equivalent, this `_run` takes no per-resource
lock.** `agent-memory-mcp` needed one because its kanban functions do a
read-modify-write (read the whole board, change one card, write it back) —
offloading to threads let two concurrent writes read the same starting
state and clobber each other. `write_page` here fully replaces a page's
content from the caller's own `content` argument; it doesn't read-and-merge
existing state, so two concurrent writes to the same page are an ordinary
last-write-wins, not a lost update — nothing to lock against. What still
matters is exactly what motivated the lock there in the first place: every
write goes through `storage._atomic_write_text` (temp file + `os.replace()`
— see below), so a lock-free concurrent read can only ever see the complete
old file or the complete new one, never a partial write.

### `_atomic_write_text` and lock-free reads

`write_page` writes via `_atomic_write_text` rather than a plain
`Path.write_text()`. Combined with no read taking any lock, this creates a
narrow, harmless TOCTOU window: a read or delete can race a concurrent
write/delete for the *same* page between resolving its path and acting on
it (e.g. `read_page`'s `path.read_text()` after a stale `is_file()` check).
`read_page`, and `delete_page`'s `unlink()`, both catch `FileNotFoundError`
around that step and treat it the same as "not found" — the same outcome a
call issued a moment earlier would have gotten anyway — rather than letting
the exception surface as a crash.

## Conventions

- Python 3.12+, managed with `uv`
- `structlog` for logging
- `ruff` for linting and formatting
- Tests use `importlib.reload(storage)` after `monkeypatch.setenv("WIKI_DIR", ...)`
  per test, since `WIKI_ROOT` is resolved once at import time — see the
  `wiki_root` fixture in `tests/test_storage.py`. `tests/conftest.py` sets a
  throwaway `WIKI_DIR` before collection so the initial module import
  (before any test's `monkeypatch` runs) doesn't fail.
- `tests/test_server.py` covers the `_run` thread-offloading/timeout wrapper
  directly, driven with plain `asyncio.run()` — no pytest-asyncio plugin
  needed, and no per-project-lock tests to mirror from `agent-memory-mcp`
  since this `_run` doesn't have one (see the Architecture section above).
