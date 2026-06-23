# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
# Install / sync dependencies
uv sync
uv sync --extra dev   # includes pytest

# Run the CLI
uv run lmstudio-code-cli
uv run lmstudio-code-cli --help

# Test
uv run pytest

# Lint
uv run ruff check lmstudio_code_cli/
uv run ruff format lmstudio_code_cli/
```

## Test structure

Tests live in `tests/` and cover the four testable modules without requiring a
live LM Studio instance:

- `tests/test_config.py` — `MCPConfig.from_mcp_json`, `Config.from_env`
- `tests/test_attachments.py` — `parse()`, `Attachment.to_openai()`
- `tests/test_tools.py` — all seven built-in tool implementations
- `tests/test_mcp_client.py` — `MCPTool.to_openai()`, `_parse_sse()`, `owns()`, `call_tool()`

`agent.py`, `main.py`, and `ui.py` are not unit-tested — they depend on a live
LM Studio stream. Verify those by running the CLI directly.

## Architecture

The agent runs a synchronous REPL loop. On each turn:

1. **`main.py`** reads user input via `prompt_toolkit`, parses any `@path` image attachments, then calls `agent.chat()`.
2. **`agent.py`** appends the message to `self.history` and enters a tool-call loop: it streams a response from LM Studio, accumulates any tool call deltas, then executes them one by one before making the next LLM call. The loop exits when the LLM returns a response with no tool calls.
3. **`tools.py`** handles the seven built-in tools (file I/O + shell). **`mcp_client.py`** handles anything the MCP gateway owns — routing is decided by `mcp.owns(name)` in `agent._execute()`.

### Tool routing

`Agent._tools` is the flat list sent to LM Studio on every request. It is built once at startup by merging `ALL_TOOLS` (built-ins) with `mcp.to_openai_tools()` (gateway tools). When the LLM returns a tool call, `Agent._execute()` checks `mcp.owns(name)` first; if true it forwards to `MCPGatewayClient.call_tool()`, otherwise to `execute_tool()` in `tools.py`.

**Critical:** LM Studio's API validator requires every tool schema to have `"properties": {}`. `MCPTool.to_openai()` enforces this with `schema.setdefault("properties", {})` because some gateway tools omit it.

### MCP client

`MCPGatewayClient` is a minimal synchronous HTTP client for the MCP 2025-03-26 Streamable HTTP transport. On construction it performs the `initialize` handshake (capturing the `Mcp-Session-Id` header), sends `notifications/initialized`, then calls `tools/list`. Subsequent `tools/call` requests reuse the session ID. Both `application/json` and `text/event-stream` response bodies are handled.

### Configuration priority

MCP gateway: `--mcp-url`/`--mcp-token` flags → `.mcp.json` in the working directory → disabled.  
`.mcp.json` format: `{"mcpServers": {"name": {"url": "...", "headers": {"Authorization": "Bearer ..."}}}}`

### Prompt input

`prompt_toolkit` runs in `multiline=True` mode. A custom `enter` key binding submits immediately when the buffer starts with `/` (slash commands); otherwise inserts a newline. `Ctrl+J` and `Esc+Enter` always submit.

### UI / colours

All terminal output goes through `ui.console` (a `rich.Console`). Agent response text streams via `ui.stream_chunk()` using the `agent.text` theme style (`#87ceeb`). Tool calls are `bold yellow`, tool results are `dim`. The theme is defined in `ui._THEME`.
