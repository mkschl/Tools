"""The allow-list and the sudo refusal are safety rules, so they get real tests."""

import pytest

from macos_diag_mcp.shell import CommandRefused, guard, run, scan

ALLOWED = frozenset(
    {"log", "pmset", "sysctl", "pgrep", "spindump", "echo", "sh", "sleep"}
)


def test_guard_allows_a_listed_binary():
    guard(["log", "show"], ALLOWED)


def test_guard_refuses_an_unlisted_binary():
    with pytest.raises(CommandRefused, match="pkill"):
        guard(["pkill", "coreautha"], ALLOWED)


def test_guard_refuses_sudo_as_the_binary():
    with pytest.raises(CommandRefused, match="never runs sudo"):
        guard(["sudo", "spindump"], ALLOWED | {"sudo"})


def test_guard_refuses_sudo_by_absolute_path():
    with pytest.raises(CommandRefused, match="never runs sudo"):
        guard(["/usr/bin/sudo", "spindump"], ALLOWED | {"sudo"})


def test_guard_allows_a_predicate_that_mentions_sudo():
    """Searching the log for a stuck sudo prompt is the point, not a violation."""
    guard(["log", "show", "--predicate", 'eventMessage CONTAINS "sudo"'], ALLOWED)


def test_guard_refuses_an_empty_command():
    with pytest.raises(CommandRefused):
        guard([], ALLOWED)


async def test_run_captures_stdout():
    result = await run(["echo", "hello"], allowed=ALLOWED, timeout=10)
    assert result.stdout.strip() == "hello"
    assert result.ok


async def test_run_times_out_without_hanging():
    result = await run(["sleep", "10"], allowed=ALLOWED, timeout=0.05)
    assert result.timed_out
    assert not result.ok


async def test_run_refuses_before_spawning():
    with pytest.raises(CommandRefused):
        await run(["rm", "-rf", "/"], allowed=ALLOWED, timeout=1)


async def _collect(cmd, **kwargs):
    lines: list[str] = []
    defaults = {
        "allowed": ALLOWED,
        "timeout": 10,
        "max_lines": 1000,
        "max_line_bytes": 65536,
    }
    result = await scan(cmd, on_line=lines.append, **(defaults | kwargs))
    return lines, result


async def test_scan_streams_every_line():
    lines, result = await _collect(["sh", "-c", "printf 'a\\nb\\nc\\n'"])
    assert lines == ["a", "b", "c"]
    assert result.lines_read == 3
    assert not result.truncated


async def test_scan_stops_at_max_lines():
    lines, result = await _collect(
        ["sh", "-c", "for i in $(seq 1 500); do echo line$i; done"], max_lines=10
    )
    assert len(lines) == 10
    assert result.truncated


async def test_scan_stopping_early_does_not_leave_the_command_running():
    _, result = await _collect(
        ["sh", "-c", "while true; do echo spam; done"], max_lines=5
    )
    assert result.truncated
    assert result.returncode is not None


async def test_scan_reports_a_timeout_with_partial_output():
    lines, result = await _collect(["sh", "-c", "echo first; sleep 10"], timeout=0.3)
    assert lines == ["first"]
    assert result.timed_out


async def test_scan_captures_stderr_on_a_clean_exit():
    _, result = await _collect(["sh", "-c", "echo oops >&2"])
    assert "oops" in result.stderr
