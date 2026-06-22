from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

_THEME = Theme(
    {
        "tool.name": "bold yellow",
        "tool.arg.key": "dim cyan",
        "tool.arg.val": "dim",
        "tool.result": "dim",
        "agent.indicator": "dim",
        "agent.text": "#87ceeb",        # soft sky-blue — distinct from white prompt input
        "error": "bold red",
        "info": "dim italic",
        "heading": "bold cyan",
        "cmd": "bold green",
    }
)

console = Console(theme=_THEME, highlight=False)


def print_welcome(model: str, url: str, cwd: str, mcp: object | None = None) -> None:
    mcp_line = ""
    if mcp is not None:
        n = len(mcp.tools)  # type: ignore[attr-defined]
        mcp_line = f"\n  [dim]MCP   :[/dim] [dim]{n} tools from gateway[/dim]"
    console.print(
        Panel.fit(
            f"[heading]lmstudio-code-cli[/heading]  [info]LM Studio coding agent[/info]\n\n"
            f"  [dim]Model :[/dim] [cyan]{model}[/cyan]\n"
            f"  [dim]API   :[/dim] [dim]{url}[/dim]\n"
            f"  [dim]CWD   :[/dim] [dim]{cwd}[/dim]{mcp_line}\n\n"
            "[info]Type your request.  "
            "[cmd]/help[/cmd]  [cmd]/tools[/cmd]  [cmd]/clear[/cmd]  "
            "[cmd]/cwd[/cmd]  [cmd]/exit[/cmd][/info]",
            border_style="cyan",
            padding=(0, 1),
        )
    )


def print_tool_call(name: str, args: dict) -> None:
    arg_parts = "  ".join(
        f"[tool.arg.key]{k}[/tool.arg.key][tool.arg.val]={repr(v)[:120]}[/tool.arg.val]"
        for k, v in args.items()
    )
    console.print(f"  [tool.name]⚙ {name}[/tool.name]  {arg_parts}")


def print_tool_result(result: str, max_lines: int = 30) -> None:
    lines = result.splitlines()
    preview = "\n".join(lines[:max_lines])
    suffix = f"\n  [dim]… {len(lines) - max_lines} more lines[/dim]" if len(lines) > max_lines else ""
    console.print(f"  [tool.result]{preview}{suffix}[/tool.result]")
    console.print()


def print_agent_indicator() -> None:
    console.print("[agent.indicator]  ●[/agent.indicator] ", end="")


def end_indicator() -> None:
    """Print newline to terminate the ● indicator line before streaming text."""
    console.print()


def stream_chunk(text: str) -> None:
    console.print(text, end="", style="agent.text", markup=False, highlight=False)


def end_stream() -> None:
    console.print()


def print_error(message: str) -> None:
    console.print(f"[error]Error: {message}[/error]")


def print_info(message: str) -> None:
    console.print(f"[info]{message}[/info]")
