import json
import sys
from pathlib import Path
from openai import OpenAI, APIConnectionError, APIStatusError

from .attachments import Attachment
from .config import Config
from .mcp_client import MCPGatewayClient
from .tools import ALL_TOOLS, execute_tool
from . import ui


def _format_api_error(exc: APIStatusError, base_url: str, model: str) -> str:
    lines = [f"LM Studio returned HTTP {exc.status_code}"]
    lines.append(f"  endpoint : {base_url}/chat/completions")
    lines.append(f"  model    : {model}")

    # Try to extract and pretty-print a JSON validation error array
    body = exc.body or {}
    raw = body.get("error", exc.message) if isinstance(body, dict) else exc.message
    try:
        errors = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(errors, list):
            for e in errors:
                path = " → ".join(str(p) for p in e.get("path", []))
                msg = e.get("message", "")
                lines.append(f"  schema   : [{path}] {msg}")
            return "\n".join(lines)
    except (json.JSONDecodeError, TypeError):
        pass

    lines.append(f"  message  : {raw}")
    return "\n".join(lines)


_SYSTEM_PROMPT = """\
You are an expert coding assistant running in the terminal. \
You help users write, read, edit, debug, and understand code.

You have two categories of tools:

BUILT-IN tools (always use these for project files and shell commands):
  read_file, write_file, edit_file  — read/write/edit files in the working directory
  list_directory, search_files, glob_files — browse and search the working directory
  run_bash — run shell commands in the working directory

MCP GATEWAY tools (use these for infrastructure: kubectl, Docker, databases, etc.):
  Everything else. Do NOT use MCP filesystem tools for project source code — \
they point to different directories and will not see your working directory.

Rules:
- Always call read_file before editing a file — never guess its contents.
- Prefer edit_file over write_file for changes to existing files.
- Use run_bash to build, test, lint, and verify changes after making them.
- Make minimal, targeted edits — do not rewrite files unless asked.
- When a tool returns an error, diagnose and fix the root cause.

Testing (mandatory):
- Always write tests when writing new code — in the same response, not as a follow-up.
- When fixing a bug, write a regression test that fails before the fix and passes after.
- Run the test suite with run_bash after every change and report the result.
  Use the right runner for the language:
    Python      → uv run pytest  (or python -m pytest)
    Go          → go test ./...
    Rust        → cargo test
    JavaScript  → npm test  (or npx jest / npx vitest)
    TypeScript  → same as JavaScript
    Swift       → swift test
    Kotlin      → ./gradlew test
- Never say "you should add tests" or "tests are left as an exercise" — write them now.

Working directory: {cwd}"""


def _load_claude_md(cwd: str) -> str:
    """Read CLAUDE.md from the working directory if present."""
    try:
        return (Path(cwd) / "CLAUDE.md").read_text()
    except OSError:
        return ""


class Agent:
    def __init__(self, config: Config, mcp: MCPGatewayClient | None = None) -> None:
        self.config = config
        self.mcp = mcp
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self.history: list[dict] = []
        self.model = config.model or self._detect_model()
        self._claude_md = _load_claude_md(config.cwd)

        # Merge built-in tools + MCP tools into one list for the LLM
        self._tools = list(ALL_TOOLS)
        if mcp:
            self._tools.extend(mcp.to_openai_tools())

    # ── Public API ────────────────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list().data]
        except APIConnectionError:
            return []

    def clear_history(self) -> None:
        self.history.clear()

    def chat(self, user_message: str, attachments: list[Attachment] | None = None) -> None:
        if attachments:
            content: list[dict] | str = [{"type": "text", "text": user_message}]
            content += [a.to_openai() for a in attachments]
        else:
            content = user_message
        self.history.append({"role": "user", "content": content})

        while True:
            result = self._call_llm()
            if result is None:
                self.history.pop()
                return

            message, tool_calls = result
            self.history.append(message)

            if not tool_calls:
                break

            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                ui.print_tool_call(name, args)
                result_text = self._execute(name, args)
                ui.print_tool_result(result_text)

                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })

    # ── Private helpers ───────────────────────────────────────────────────────

    def _execute(self, name: str, args: dict) -> str:
        """Route a tool call to the MCP gateway or the built-in handler."""
        if self.mcp and self.mcp.owns(name):
            try:
                return self.mcp.call_tool(name, args)
            except Exception as exc:
                return f"MCP gateway error: {exc}"
        return execute_tool(name, args, self.config.cwd)

    def _detect_model(self) -> str:
        try:
            models = self.client.models.list().data
        except APIConnectionError as exc:
            ui.print_error(
                f"Cannot connect to LM Studio at {self.config.base_url}\n"
                f"  Make sure LM Studio is running and a model is loaded.\n"
                f"  ({exc})"
            )
            sys.exit(1)

        if not models:
            ui.print_error("No models loaded in LM Studio — load a model and try again.")
            sys.exit(1)

        return models[0].id

    def _system_message(self) -> dict:
        content = _SYSTEM_PROMPT.format(cwd=self.config.cwd)
        if self._claude_md:
            content += f"\n\n## Project instructions (CLAUDE.md)\n\n{self._claude_md}"
        return {"role": "system", "content": content}

    def _call_llm(self) -> tuple[dict, list[dict]] | None:
        ui.print_agent_indicator()

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[self._system_message(), *self.history],
                tools=self._tools,
                tool_choice="auto",
                max_tokens=self.config.max_tokens,
                stream=True,
            )
        except APIConnectionError as exc:
            ui.end_indicator()
            ui.print_error(
                f"Cannot reach LM Studio at {self.config.base_url}\n"
                f"  {exc}"
            )
            return None
        except APIStatusError as exc:
            ui.end_indicator()
            ui.print_error(_format_api_error(exc, self.config.base_url, self.model))
            return None

        text_parts: list[str] = []
        pending_tool_calls: dict[int, dict] = {}
        first_output = True

        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    if first_output:
                        ui.end_indicator()
                        first_output = False
                    ui.stream_chunk(delta.content)
                    text_parts.append(delta.content)

                if delta.tool_calls:
                    if first_output:
                        ui.end_indicator()
                        first_output = False
                    for tc in delta.tool_calls:
                        slot = pending_tool_calls.setdefault(
                            tc.index,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments

        except KeyboardInterrupt:
            if text_parts:
                ui.end_stream()
            ui.print_info("Interrupted.")
            return None

        if text_parts:
            ui.end_stream()
        elif first_output:
            ui.end_indicator()

        tool_calls_list = list(pending_tool_calls.values())
        message: dict = {"role": "assistant"}
        if text_parts:
            message["content"] = "".join(text_parts)
        if tool_calls_list:
            message["content"] = message.get("content")
            message["tool_calls"] = tool_calls_list

        return message, tool_calls_list
