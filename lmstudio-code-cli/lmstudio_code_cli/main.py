import os
import tomllib
from pathlib import Path

import click
import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .attachments import parse as parse_attachments
from .defaults import DEFAULTS

from .config import Config, MCPConfig
from .agent import Agent
from .mcp_client import MCPGatewayClient
from . import ui

_CONFIG_PATH = Path.home() / ".lmstudio-code-cli.toml"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def _save_config(updates: dict) -> None:
    existing = _load_config()
    existing.update(updates)
    lines = []
    for k, v in existing.items():
        lines.append(f'{k} = "{v}"\n' if isinstance(v, str) else f"{k} = {v}\n")
    _CONFIG_PATH.write_text("".join(lines))

def _prompt_style() -> Style:
    return Style.from_dict({"prompt": f"bold {ui.THEMES[ui.active_theme()]['_prompt']}"})


def _make_session(history_path: Path, agent: Agent) -> PromptSession:
    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("escape", " ")  # Option+Space on macOS (sends ESC Space)
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    return PromptSession(
        history=FileHistory(str(history_path)),
        style=_prompt_style(),
        multiline=True,
        key_bindings=kb,
        prompt_continuation=lambda _width, _line, _wrap: "  ",
    )

_HELP_TEXT = f"""\
[bold]Slash commands[/bold]
  [cmd]/clear[/cmd]           — Clear conversation history
  [cmd]/cls[/cmd]             — Clear the terminal screen
  [cmd]/retry[/cmd]           — Remove last exchange and resend the message
  [cmd]/undo[/cmd]            — Remove last exchange without resending
  [cmd]/save [file][/cmd]     — Save conversation history (default: {DEFAULTS['conversation_file']})
  [cmd]/load [file][/cmd]     — Load conversation history (default: {DEFAULTS['conversation_file']})
  [cmd]/model [id][/cmd]      — Switch model interactively, or directly with an id
  [cmd]/models[/cmd]          — List models loaded in LM Studio
  [cmd]/theme [name][/cmd]    — Switch color theme interactively, or directly with a name
  [cmd]/cwd [path][/cmd]      — Show or change the working directory
  [cmd]/mcp [group,...][/cmd]  — Enable MCP tool groups; no args opens picker; /mcp off disables
  [cmd]/tools[/cmd]           — List all available tools (built-in + MCP)
  [cmd]/help[/cmd]            — Show this help
  [cmd]/exit[/cmd], [cmd]/quit[/cmd]   — Exit

[bold]Tips[/bold]
  • [bold]Enter[/bold] submits — use [bold]Opt+Space[/bold] to insert a newline
  • [bold]Opt+Space[/bold] requires "Use Option as Meta key" in Terminal.app preferences (or equivalent in iTerm2/Warp)
  • Set [bold]LMSTUDIO_URL[/bold] / [bold]LMSTUDIO_MODEL[/bold] env vars instead of flags
  • Place a [bold].mcp.json[/bold] in your working directory for automatic MCP gateway discovery
  • Press [bold]Ctrl+C[/bold] to interrupt a running response
  • Press [bold]Ctrl+D[/bold] or type [cmd]/exit[/cmd] to quit"""


@click.command()
@click.option("--url", envvar="LMSTUDIO_URL", default=None,
              show_envvar=True, help=f"LM Studio API base URL (default: {DEFAULTS['url']})")
@click.option("--api-key", envvar="LMSTUDIO_API_KEY", default=DEFAULTS["api_key"],
              show_default=True, show_envvar=True, help="LM Studio API key")
@click.option("--model", envvar="LMSTUDIO_MODEL", default=None,
              show_envvar=True, help="Model ID (auto-detects first loaded model if omitted)")
@click.option("--cwd", "working_dir", default=None,
              help="Working directory for tool operations (default: current directory)")
@click.option("--max-tokens", envvar="LMSTUDIO_MAX_TOKENS", default=None,
              show_envvar=True, type=int, help=f"Max tokens per response (default: {DEFAULTS['max_tokens']})")
@click.option("--mcp-url", envvar="LMSTUDIO_MCP_URL", default="",
              show_envvar=True, help="MCP gateway URL (overrides .mcp.json)")
@click.option("--mcp-token", envvar="LMSTUDIO_MCP_TOKEN", default="",
              show_envvar=True, help="MCP gateway Bearer token (overrides .mcp.json)")
@click.option("--theme", envvar="LMSTUDIO_THEME",
              default=None, show_envvar=True,
              type=click.Choice(ui.theme_names(), case_sensitive=False),
              help="Color theme (overrides saved preference)")
def cli(
    url: str | None,
    api_key: str,
    model: str | None,
    working_dir: str | None,
    max_tokens: int | None,
    mcp_url: str,
    mcp_token: str,
    theme: str | None,
) -> None:
    """Terminal coding agent powered by LM Studio."""
    cwd = os.path.abspath(working_dir) if working_dir else os.getcwd()

    cfg = _load_config()
    resolved_url = url or cfg.get("url", DEFAULTS["url"])
    resolved_model = model or cfg.get("model", "")
    resolved_max_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", DEFAULTS["max_tokens"])
    resolved_theme = theme or cfg.get("theme", DEFAULTS["theme"])

    # Persist any values that were explicitly provided via CLI/env
    updates = {k: v for k, v in {
        "url": url, "model": model, "max_tokens": max_tokens, "theme": theme,
    }.items() if v is not None}
    if updates:
        _save_config(updates)

    ui.set_theme(resolved_theme)

    # Build MCP config: CLI flags → .mcp.json → nothing
    mcp_cfg: MCPConfig | None = None
    if mcp_url and mcp_token:
        mcp_cfg = MCPConfig(url=mcp_url, token=mcp_token)
    else:
        mcp_json = Path(cwd) / ".mcp.json"
        if mcp_json.exists():
            mcp_cfg = MCPConfig.from_mcp_json(mcp_json)

    config = Config(
        base_url=resolved_url.rstrip("/"),
        api_key=api_key,
        model=resolved_model,
        cwd=cwd,
        max_tokens=resolved_max_tokens,
        mcp=mcp_cfg,
    )

    # Connect to MCP gateway if configured; no tools are sent to the model until
    # the user runs /mcp to select which groups they need.
    mcp_client: MCPGatewayClient | None = None
    if config.mcp:
        ui.print_info(f"Connecting to MCP gateway at {config.mcp.url} …")
        try:
            mcp_client = MCPGatewayClient(url=config.mcp.url, token=config.mcp.token)
            ui.print_info(
                f"MCP gateway: {len(mcp_client.tools)} tools available "
                f"(use /mcp to enable)"
            )
        except Exception as exc:
            ui.print_error(f"MCP gateway connection failed: {exc}")
            ui.print_info("Continuing without MCP gateway.")

    agent = Agent(config, mcp=mcp_client)
    ui.print_welcome(agent.model, config.base_url, config.cwd, mcp_client)

    history_path = Path.home() / DEFAULTS["history_file"]
    session = _make_session(history_path, agent)

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
            resend = _handle_slash(text, agent, session=session)
            if text.lower() in ("/exit", "/quit"):
                break
            if resend:
                try:
                    agent.chat(resend)
                except KeyboardInterrupt:
                    ui.console.print()
                    ui.print_info("Interrupted.")
                except Exception as exc:  # noqa: BLE001
                    ui.print_error(str(exc))
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


def _model_interactive(agent: Agent) -> None:
    """Interactive selector for switching the active LM Studio model."""
    models = agent.list_models()
    if not models:
        ui.print_error("No models found — is LM Studio running?")
        return

    choices = [
        questionary.Choice(title=m, value=m, checked=(m == agent.model))
        for m in models
    ]

    selected = questionary.select(
        "Select model  (arrow keys to move, enter to confirm, ctrl+c to cancel)",
        choices=choices,
        default=agent.model if agent.model in models else models[0],
    ).ask()

    if selected is None:
        ui.print_info("Cancelled.")
        return

    agent.model = selected
    _save_config({"model": selected})
    ui.print_info(f"Model: {selected}")


def _mcp_interactive(agent: Agent) -> None:
    """Interactive checkbox picker for MCP tool groups."""
    if not agent.mcp:
        return
    groups: dict[str, list[str]] = {}
    for t in agent.mcp.tools:
        prefix = t.name.split("_")[0]
        groups.setdefault(prefix, []).append(t.name)

    active = agent.active_mcp_tool_names()
    choices = [
        questionary.Choice(
            title=f"{prefix}  ({len(names)} tools)",
            value=prefix,
            checked=any(n in active for n in names),
        )
        for prefix, names in sorted(groups.items())
    ]

    selected = questionary.checkbox(
        "Select MCP tool groups  (space to toggle, enter to confirm, ctrl+c to cancel)",
        choices=choices,
    ).ask()

    if selected is None:
        ui.print_info("Cancelled.")
        return

    count = agent.enable_mcp_tools(selected)
    if count:
        ui.print_info(f"{count} MCP tools enabled ({', '.join(selected)})")
    else:
        ui.print_info("MCP tools disabled.")


def _theme_interactive(session: "PromptSession") -> None:
    names = ui.theme_names()
    selected = questionary.select(
        "Select theme  (arrow keys to move, enter to confirm, ctrl+c to cancel)",
        choices=names,
        default=ui.active_theme(),
    ).ask()
    if selected is None:
        ui.print_info("Cancelled.")
        return
    ui.set_theme(selected)
    session.style = _prompt_style()
    _save_config({"theme": selected})
    ui.print_info(f"Theme: {selected}")


def _handle_slash(text: str, agent: Agent, session: "PromptSession | None" = None) -> str | None:
    """Handle a slash command. Returns a message to resend (for /retry), or None."""
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    match cmd:
        case "/exit" | "/quit":
            ui.print_info("Bye!")

        case "/cls":
            click.clear()
            ui.print_welcome(agent.model, agent.config.base_url, agent.config.cwd)

        case "/clear":
            agent.clear_history()
            ui.print_info("Conversation cleared.")

        case "/retry":
            msg = agent.pop_last_user_message()
            if msg:
                ui.print_info(f"Retrying: {msg[:80]}{'…' if len(msg) > 80 else ''}")
                return msg
            ui.print_error("Nothing to retry.")

        case "/undo":
            msg = agent.pop_last_user_message()
            if msg:
                ui.print_info(f"Removed: {msg[:80]}{'…' if len(msg) > 80 else ''}")
            else:
                ui.print_error("Nothing to undo.")

        case "/save":
            path = arg or DEFAULTS["conversation_file"]
            try:
                agent.save_history(path)
                ui.print_info(f"Saved {len(agent.history)} messages to {path}")
            except OSError as exc:
                ui.print_error(f"Could not save: {exc}")

        case "/load":
            path = arg or DEFAULTS["conversation_file"]
            try:
                count = agent.load_history(path)
                ui.print_info(f"Loaded {count} messages from {path}")
            except (OSError, ValueError) as exc:
                ui.print_error(f"Could not load: {exc}")

        case "/model":
            if arg:
                agent.model = arg
                _save_config({"model": arg})
                ui.print_info(f"Model: {arg}")
            else:
                _model_interactive(agent)

        case "/theme":
            if arg:
                if ui.set_theme(arg):
                    if session:
                        session.style = _prompt_style()
                    _save_config({"theme": arg})
                    ui.print_info(f"Theme: {arg}")
                else:
                    ui.print_error(f"Unknown theme: {arg}  (available: {', '.join(ui.theme_names())})")
            elif session:
                _theme_interactive(session)
            else:
                ui.print_error("No session available for interactive theme picker.")

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

        case "/mcp":
            if not agent.mcp:
                ui.print_error("No MCP gateway connected.")
            elif not arg:
                _mcp_interactive(agent)
            elif arg.lower() == "off":
                agent.enable_mcp_tools([])
                ui.print_info("MCP tools disabled.")
            else:
                prefixes = [p.strip() for p in arg.split(",") if p.strip()]
                count = agent.enable_mcp_tools(prefixes)
                if count:
                    ui.print_info(f"{count} MCP tools enabled ({', '.join(prefixes)})")
                else:
                    ui.print_error(f"No MCP tools matched: {', '.join(prefixes)}")

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
            tokens = agent.estimated_tokens
            token_info = f"  [info]~{tokens:,} tokens in current history[/info]\n" if tokens else ""
            ui.console.print(token_info + _HELP_TEXT)

        case _:
            ui.print_error(f"Unknown command: {cmd}  (type /help for commands)")

    return None
