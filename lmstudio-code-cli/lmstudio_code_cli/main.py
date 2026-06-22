import os
from pathlib import Path

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .attachments import parse as parse_attachments
from .config import Config, MCPConfig
from .agent import Agent
from .mcp_client import MCPGatewayClient
from . import ui

_PROMPT_STYLE = Style.from_dict({
    "prompt": "bold ansicyan",
    "bottom-toolbar": "bg:#1a1a1a #555555",
})

_TOOLBAR = "  [Enter] newline  (/ commands submit immediately)   [Esc+Enter] or [Ctrl+J] submit   [Ctrl+D] quit  "

def _make_session(history_path: Path) -> PromptSession:
    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event) -> None:
        buf = event.current_buffer
        if buf.text.startswith("/"):
            buf.validate_and_handle()
        else:
            buf.insert_text("\n")

    @kb.add("c-j")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    return PromptSession(
        history=FileHistory(str(history_path)),
        style=_PROMPT_STYLE,
        multiline=True,
        key_bindings=kb,
        prompt_continuation=lambda _width, _line, _wrap: "  ",
        bottom_toolbar=_TOOLBAR,
    )

_HELP_TEXT = """\
[bold]Slash commands[/bold]
  [cmd]/clear[/cmd]           — Clear conversation history
  [cmd]/model [id][/cmd]      — Show current model or switch to a different one
  [cmd]/models[/cmd]          — List models loaded in LM Studio
  [cmd]/cwd [path][/cmd]      — Show or change the working directory
  [cmd]/tools[/cmd]           — List all available tools (built-in + MCP)
  [cmd]/help[/cmd]            — Show this help
  [cmd]/exit[/cmd], [cmd]/quit[/cmd]   — Exit

[bold]Tips[/bold]
  • [bold]Enter[/bold] adds a newline — use [bold]Esc+Enter[/bold] or [bold]Ctrl+J[/bold] to submit
  • Set [bold]LMSTUDIO_URL[/bold] / [bold]LMSTUDIO_MODEL[/bold] env vars instead of flags
  • Place a [bold].mcp.json[/bold] in your working directory for automatic MCP gateway discovery
  • Press [bold]Ctrl+C[/bold] to interrupt a running response
  • Press [bold]Ctrl+D[/bold] or type [cmd]/exit[/cmd] to quit"""


@click.command()
@click.option("--url", envvar="LMSTUDIO_URL", default="http://localhost:1234/v1",
              show_default=True, show_envvar=True, help="LM Studio API base URL")
@click.option("--api-key", envvar="LMSTUDIO_API_KEY", default="lm-studio",
              show_default=True, show_envvar=True, help="LM Studio API key")
@click.option("--model", envvar="LMSTUDIO_MODEL", default="",
              show_envvar=True, help="Model ID (auto-detects first loaded model if omitted)")
@click.option("--cwd", "working_dir", default=None,
              help="Working directory for tool operations (default: current directory)")
@click.option("--max-tokens", envvar="LMSTUDIO_MAX_TOKENS", default=8192,
              show_default=True, show_envvar=True, type=int, help="Max tokens per response")
@click.option("--mcp-url", envvar="LMSTUDIO_MCP_URL", default="",
              show_envvar=True, help="MCP gateway URL (overrides .mcp.json)")
@click.option("--mcp-token", envvar="LMSTUDIO_MCP_TOKEN", default="",
              show_envvar=True, help="MCP gateway Bearer token (overrides .mcp.json)")
def cli(
    url: str,
    api_key: str,
    model: str,
    working_dir: str | None,
    max_tokens: int,
    mcp_url: str,
    mcp_token: str,
) -> None:
    """Terminal coding agent powered by LM Studio."""
    cwd = os.path.abspath(working_dir) if working_dir else os.getcwd()

    # Build MCP config: CLI flags → .mcp.json → nothing
    mcp_cfg: MCPConfig | None = None
    if mcp_url and mcp_token:
        mcp_cfg = MCPConfig(url=mcp_url, token=mcp_token)
    else:
        mcp_json = Path(cwd) / ".mcp.json"
        if mcp_json.exists():
            mcp_cfg = MCPConfig.from_mcp_json(mcp_json)

    config = Config(
        base_url=url.rstrip("/"),
        api_key=api_key,
        model=model,
        cwd=cwd,
        max_tokens=max_tokens,
        mcp=mcp_cfg,
    )

    # Connect to MCP gateway if configured
    mcp_client: MCPGatewayClient | None = None
    if config.mcp:
        ui.print_info(f"Connecting to MCP gateway at {config.mcp.url} …")
        try:
            mcp_client = MCPGatewayClient(url=config.mcp.url, token=config.mcp.token)
            ui.print_info(f"MCP gateway: {len(mcp_client.tools)} tools available")
        except Exception as exc:
            ui.print_error(f"MCP gateway connection failed: {exc}")
            ui.print_info("Continuing without MCP gateway.")

    agent = Agent(config, mcp=mcp_client)
    ui.print_welcome(agent.model, config.base_url, config.cwd, mcp_client)

    history_path = Path.home() / ".lmstudio-code-cli-history"
    session = _make_session(history_path)

    while True:
        try:
            raw = session.prompt([("class:prompt", "❯ ")])
        except KeyboardInterrupt:
            continue
        except EOFError:
            ui.print_info("Bye!")
            break

        text = raw.strip()
        if not text:
            continue

        if text.startswith("/"):
            _handle_slash(text, agent)
            if text.lower() in ("/exit", "/quit"):
                break
            continue

        try:
            clean, attachments, errors = parse_attachments(text, agent.config.cwd)
            for err in errors:
                ui.print_error(err)
            for a in attachments:
                ui.print_info(f"Attaching {a.path.name} ({a.size_kb:.1f} KB, {a.mime_type})")
            if not clean and not attachments:
                continue
            agent.chat(clean or text, attachments=attachments or None)
        except KeyboardInterrupt:
            ui.console.print()
            ui.print_info("Interrupted.")
        except Exception as exc:  # noqa: BLE001
            ui.print_error(str(exc))

    if mcp_client:
        mcp_client.close()


def _handle_slash(text: str, agent: Agent) -> None:
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    match cmd:
        case "/exit" | "/quit":
            ui.print_info("Bye!")

        case "/clear":
            agent.clear_history()
            ui.print_info("Conversation cleared.")

        case "/model":
            if arg:
                agent.model = arg
                ui.print_info(f"Model: {arg}")
            else:
                ui.print_info(f"Model: {agent.model}")

        case "/models":
            models = agent.list_models()
            if models:
                ui.console.print("[info]Loaded models:[/info]")
                for m in models:
                    marker = "[bold cyan]●[/bold cyan]" if m == agent.model else " "
                    ui.console.print(f"  {marker} {m}")
            else:
                ui.print_error("No models found — is LM Studio running?")

        case "/cwd":
            if arg:
                new_cwd = os.path.expanduser(arg)
                new_cwd = os.path.abspath(new_cwd)
                if os.path.isdir(new_cwd):
                    agent.config.cwd = new_cwd
                    ui.print_info(f"Working directory: {new_cwd}")
                else:
                    ui.print_error(f"Not a directory: {new_cwd}")
            else:
                ui.print_info(f"Working directory: {agent.config.cwd}")

        case "/tools":
            from .tools import ALL_TOOLS
            ui.console.print("[bold]Built-in tools:[/bold]")
            for t in ALL_TOOLS:
                name = t["function"]["name"]
                desc = t["function"]["description"].split("\n")[0][:80]
                ui.console.print(f"  [tool.name]{name}[/tool.name]  [dim]{desc}[/dim]")
            if agent.mcp:
                ui.console.print(f"\n[bold]MCP gateway tools[/bold] [dim]({len(agent.mcp.tools)})[/dim]:")
                for t in agent.mcp.tools:
                    desc = t.description.split("\n")[0][:80]
                    ui.console.print(f"  [tool.name]{t.name}[/tool.name]  [dim]{desc}[/dim]")

        case "/help":
            ui.console.print(_HELP_TEXT)

        case _:
            ui.print_error(f"Unknown command: {cmd}  (type /help for commands)")
