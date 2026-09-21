# Tools

A collection of small, focused [MCP](https://modelcontextprotocol.io) servers and a terminal coding
agent, built for day-to-day use with local LLMs and Claude Code. Each subproject is an independent
Python package with its own `pyproject.toml`, `.venv`, and test suite — this repo just groups them
together and shares an editor/type-checking environment (see [Multi-Python-Project layout](#layout)
below).

## Projects

| Project | Description |
| --- | --- |
| [`lmstudio-code-cli`](lmstudio-code-cli/) | Terminal-based coding agent powered by LM Studio, with theming, MCP gateway integration, and file attachments |
| [`agent-memory-mcp`](agent-memory-mcp/) | Shared persistent memory MCP server (decision log, kanban boards, notes) for agent workflows |
| [`cad-photo-mcp`](cad-photo-mcp/) | MCP server for measuring parts in calibrated photos and overlaying OpenSCAD models on them at true scale |
| [`kubectl-mcp`](kubectl-mcp/) | Generic MCP server exposing `kubectl` cluster operations as tools |
| [`parallels-mcp`](parallels-mcp/) | MCP server for Parallels Desktop VM lifecycle control via `prlctl` |
| [`threemf-paint-mcp`](threemf-paint-mcp/) | MCP server for inspecting and recoloring Bambu Studio / OrcaSlicer `.3mf` multi-color paint data |
| [`wiki-mcp`](wiki-mcp/) | MCP server for read/search/write access to an external markdown wiki repo (link resolution, backlinks) |

Each project has its own `CLAUDE.md` with commands, architecture notes, and testing instructions.

## Getting started

Every subproject uses [`uv`](https://github.com/astral-sh/uv) for dependency management:

```bash
cd <project>
uv sync --extra dev
uv run pytest
```

Run a server or CLI directly with `uv run <entry-point>` from inside its own directory — see the
project's own README/CLAUDE.md for specifics.

## Layout

This repo follows a multi-Python-project pattern: the root `pyproject.toml` aggregates every
subproject's dependencies purely so VS Code/Pylance can resolve imports across all of them from a
single interpreter (`uv sync` at the root, see `.vscode/settings.json`). It is not a real package
and is never used for running or deploying any of the tools — each subproject remains fully
self-contained with its own lockfile and virtual environment.

## License

MIT — see [LICENSE](LICENSE).
