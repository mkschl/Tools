import sys
import threading

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.theme import Theme

from .themes import DEFAULT_THEME, THEMES

_active_name: str = DEFAULT_THEME
_code_theme: str = THEMES[DEFAULT_THEME]["_code"]
_RICH_KEYS = {k for k in THEMES[DEFAULT_THEME] if not k.startswith("_")}


def _build_rich_theme(data: dict[str, str]) -> Theme:
    return Theme({k: v for k, v in data.items() if k in _RICH_KEYS})


_THEME: Theme = _build_rich_theme(THEMES[DEFAULT_THEME])
console: Console = Console(theme=_THEME, highlight=False)


def set_theme(name: str) -> bool:
    """Switch to a named theme. Returns False if the name is unknown."""
    global _THEME, console, _active_name, _code_theme
    if name not in THEMES:
        return False
    data = THEMES[name]
    _active_name = name
    _code_theme = data["_code"]
    _THEME = _build_rich_theme(data)
    console = Console(theme=_THEME, highlight=False)
    return True


def theme_names() -> list[str]:
    return list(THEMES)


def active_theme() -> str:
    return _active_name


def _meta(key: str) -> str:
    return THEMES[_active_name][key]


# ── Spinner ────────────────────────────────────────────────────────────────────

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spinner_stop = threading.Event()
_spinner_thread: threading.Thread | None = None


# ── Public UI functions ────────────────────────────────────────────────────────

def print_welcome(model: str, url: str, cwd: str, mcp: object | None = None) -> None:
    mcp_line = ""
    if mcp is not None:
        n = len(mcp.tools)  # type: ignore[attr-defined]
        mcp_line = f"\n  [info]MCP   :[/info] {n} tools from gateway"
    border = _meta("_border")
    model_color = _meta("_model")
    console.print(
        Panel.fit(
            f"[heading]lmstudio-code-cli[/heading]  [info]LM Studio coding agent[/info]\n\n"
            f"  [info]Model :[/info] [{model_color}]{model}[/{model_color}]\n"
            f"  [info]API   :[/info] {url}\n"
            f"  [info]CWD   :[/info] {cwd}{mcp_line}\n\n"
            "For more options type [cmd]/help[/cmd]",
            border_style=border,
            padding=(0, 1),
        )
    )


def print_tool_call(name: str, args: dict) -> None:
    arg_parts = "  ".join(
        f"[tool.arg.key]{k}[/tool.arg.key][tool.arg.val]={repr(v)[:120]}[/tool.arg.val]"
        for k, v in args.items()
    )
    console.print(f"  [tool.name]⚙ {name}[/tool.name]  {arg_parts}")


def print_tool_result(result: str) -> None:
    lines = result.splitlines()
    if len(lines) <= 1 and len(result) <= 80:
        summary = result or "(empty)"
    else:
        summary = f"{len(lines)} lines"
    console.print(f"  [tool.result]→ {summary}[/tool.result]")


def print_agent_indicator() -> None:
    global _spinner_thread
    _spinner_stop.clear()

    def _spin() -> None:
        i = 0
        while not _spinner_stop.wait(0.08):
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            sys.stdout.write(f"\r  {frame} ")
            sys.stdout.flush()
            i += 1

    _spinner_thread = threading.Thread(target=_spin, daemon=True)
    _spinner_thread.start()


def end_indicator() -> None:
    global _spinner_thread
    _spinner_stop.set()
    if _spinner_thread:
        _spinner_thread.join(timeout=0.3)
        _spinner_thread = None
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def render_response(text: str) -> None:
    console.print(Markdown(text, code_theme=_code_theme), style="agent.text")


def print_error(message: str) -> None:
    console.print(f"[error]Error: {message}[/error]")


def print_info(message: str) -> None:
    console.print(f"[info]{message}[/info]")
