import io
import time
from unittest.mock import MagicMock, patch

from rich.console import Console

import lmstudio_code_cli.ui as ui_module


def _capture(fn, *args, **kwargs) -> str:
    """Run fn with ui.console redirected to a StringIO, return plain text output."""
    buf = io.StringIO()
    test_console = Console(file=buf, highlight=False, no_color=True, theme=ui_module._THEME)
    with patch.object(ui_module, "console", test_console):
        fn(*args, **kwargs)
    return buf.getvalue()


# ── theme system ──────────────────────────────────────────────────────────────

def test_theme_names_returns_all_themes():
    names = ui_module.theme_names()
    assert "one-dark" in names
    assert "dracula" in names
    assert "nord" in names
    assert "gruvbox" in names
    assert "catppuccin" in names


def test_set_theme_switches_active_theme():
    original = ui_module.active_theme()
    try:
        ui_module.set_theme("dracula")
        assert ui_module.active_theme() == "dracula"
    finally:
        ui_module.set_theme(original)


def test_set_theme_returns_false_for_unknown():
    assert ui_module.set_theme("nonexistent") is False


def test_set_theme_rebuilds_console():
    original = ui_module.active_theme()
    try:
        old_console = ui_module.console
        ui_module.set_theme("nord")
        assert ui_module.console is not old_console
    finally:
        ui_module.set_theme(original)


def test_set_theme_updates_code_theme():
    original = ui_module.active_theme()
    try:
        ui_module.set_theme("gruvbox")
        assert ui_module._code_theme == ui_module.THEMES["gruvbox"]["_code"]
    finally:
        ui_module.set_theme(original)


# ── print_tool_result ──────────────────────────────────────────────────────────

def test_tool_result_short_single_line_shows_value():
    output = _capture(ui_module.print_tool_result, "ok")
    assert "ok" in output


def test_tool_result_empty_string_shows_placeholder():
    output = _capture(ui_module.print_tool_result, "")
    assert "(empty)" in output


def test_tool_result_exactly_80_chars_shows_value():
    output = _capture(ui_module.print_tool_result, "x" * 80)
    assert "x" * 80 in output


def test_tool_result_81_chars_shows_line_count():
    output = _capture(ui_module.print_tool_result, "x" * 81)
    assert "1 lines" in output


def test_tool_result_multiline_shows_count():
    output = _capture(ui_module.print_tool_result, "a\nb\nc")
    assert "3 lines" in output


def test_tool_result_multiline_does_not_show_content():
    output = _capture(ui_module.print_tool_result, "secret\ndata\nhere")
    assert "secret" not in output


def test_tool_result_prefixed_with_arrow():
    output = _capture(ui_module.print_tool_result, "done")
    assert "→" in output


# ── print_tool_call ────────────────────────────────────────────────────────────

def test_tool_call_shows_tool_name():
    output = _capture(ui_module.print_tool_call, "read_file", {"path": "foo.py"})
    assert "read_file" in output


def test_tool_call_shows_arg_key_and_value():
    output = _capture(ui_module.print_tool_call, "run_bash", {"command": "ls"})
    assert "command" in output
    assert "ls" in output


def test_tool_call_truncates_long_arg_value():
    long_val = "x" * 200
    output = _capture(ui_module.print_tool_call, "write_file", {"content": long_val})
    # repr truncated at 120 chars — the full 200-char value must not appear
    assert long_val not in output


def test_tool_call_shows_multiple_args():
    output = _capture(ui_module.print_tool_call, "edit_file", {
        "path": "a.py", "old_string": "foo", "new_string": "bar"
    })
    assert "path" in output
    assert "old_string" in output
    assert "new_string" in output


# ── spinner (print_agent_indicator / end_indicator) ───────────────────────────

def test_spinner_thread_starts_on_indicator():
    with patch("sys.stdout.write"), patch("sys.stdout.flush"):
        ui_module.print_agent_indicator()
        try:
            assert ui_module._spinner_thread is not None
            assert ui_module._spinner_thread.is_alive()
        finally:
            ui_module.end_indicator()


def test_spinner_thread_stops_on_end_indicator():
    with patch("sys.stdout.write"), patch("sys.stdout.flush"):
        ui_module.print_agent_indicator()
        ui_module.end_indicator()
        # give the thread a moment to fully exit
        time.sleep(0.05)
        assert not ui_module._spinner_stop.is_set() or (
            ui_module._spinner_thread is None or not ui_module._spinner_thread.is_alive()
        )


def test_spinner_stop_event_set_after_end_indicator():
    with patch("sys.stdout.write"), patch("sys.stdout.flush"):
        ui_module.print_agent_indicator()
        ui_module.end_indicator()
        assert ui_module._spinner_stop.is_set()


# ── render_response ───────────────────────────────────────────────────────────

# ── print_welcome ──────────────────────────────────────────────────────────────

def test_print_welcome_shows_model_url_cwd():
    output = _capture(ui_module.print_welcome, "gemma-4", "http://localhost:1234/v1", "/tmp/project")
    assert "gemma-4" in output
    assert "localhost:1234" in output
    assert "/tmp/project" in output


def test_print_welcome_without_mcp_shows_no_mcp_line():
    output = _capture(ui_module.print_welcome, "m", "http://x", "/tmp", mcp=None)
    assert "MCP" not in output


def test_print_welcome_with_mcp_shows_tool_count():
    mcp = MagicMock()
    mcp.tools = [MagicMock()] * 42
    output = _capture(ui_module.print_welcome, "m", "http://x", "/tmp", mcp=mcp)
    assert "42" in output


# ── print_error / print_info ───────────────────────────────────────────────────

def test_print_error_contains_message():
    output = _capture(ui_module.print_error, "something went wrong")
    assert "something went wrong" in output


def test_print_info_contains_message():
    output = _capture(ui_module.print_info, "connected successfully")
    assert "connected successfully" in output


# ── render_response ──────────────────────────────────────────────────────────────

def test_render_response_plain_text():
    output = _capture(ui_module.render_response, "Hello world")
    assert "Hello world" in output


def test_render_response_highlights_code_block():
    output = _capture(ui_module.render_response, "```python\nprint('hi')\n```")
    assert "print" in output


def test_render_response_renders_bold():
    output = _capture(ui_module.render_response, "This is **important**")
    assert "important" in output


# ── end_indicator_clears_line ─────────────────────────────────────────────────

def test_end_indicator_clears_line():
    writes = []
    with patch("sys.stdout.write", side_effect=writes.append), patch("sys.stdout.flush"):
        ui_module.print_agent_indicator()
        ui_module.end_indicator()
    assert any("\r\033[K" in w for w in writes)
