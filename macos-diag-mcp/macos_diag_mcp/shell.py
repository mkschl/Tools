"""Bounded, read-only subprocess execution — the only way this server runs anything.

Two safety rules are enforced here rather than left to each tool's good
behaviour: the allow-list means a binary that could kill a process or change a
setting is simply not reachable, and sudo is refused outright. The refusal is
not caution for its own sake — with pam_tid enabled, a sudo call opens a Touch
ID prompt, and a pending prompt is itself one of the failure modes this server
exists to diagnose.

Commands are executed as an argv list, never through a shell, so a predicate or
a path coming from the caller cannot become another command.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass


class CommandRefused(Exception):
    """The command is not one this read-only server is allowed to run."""


@dataclass(frozen=True)
class Completed:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class ScanResult:
    """Outcome of streaming a command's output past a line handler."""

    lines_read: int
    truncated: bool
    timed_out: bool
    returncode: int | None
    stderr: str


# Refused as a binary even if one were ever added to the allow-list. Arguments
# are deliberately not scanned for these words: a log predicate searching the
# log *for* sudo is exactly what hunting a stuck Touch ID prompt looks like, and
# no allow-listed binary can execute another command anyway.
ELEVATORS = frozenset({"sudo", "su", "doas", "osascript"})


def guard(cmd: list[str], allowed: frozenset[str]) -> None:
    if not cmd:
        raise CommandRefused("empty command")
    binary = cmd[0].rsplit("/", 1)[-1]
    if binary in ELEVATORS:
        raise CommandRefused(
            f"this server never runs {binary}: it would open a Touch ID prompt, and "
            "a pending prompt can lock the menu bar. Run the command yourself."
        )
    if binary not in allowed:
        raise CommandRefused(
            f"'{binary}' is not one of the read-only commands this server may run "
            f"({', '.join(sorted(allowed))})"
        )


async def run(cmd: list[str], *, allowed: frozenset[str], timeout: float) -> Completed:
    """Run a command to completion and capture its output."""
    guard(cmd, allowed)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _terminate(proc)
        return Completed("", f"[timeout after {timeout:g}s]", -1, True)
    return Completed(
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        proc.returncode or 0,
        False,
    )


async def scan(
    cmd: list[str],
    *,
    allowed: frozenset[str],
    timeout: float,
    max_lines: int,
    max_line_bytes: int,
    on_line: Callable[[str], None],
) -> ScanResult:
    """Stream a command's stdout line by line into `on_line`.

    `log show` over a wide window can emit hundreds of thousands of lines, so
    output is aggregated as it arrives and never held whole. Hitting `max_lines`
    or the timeout stops the command and reports partial results, which beat
    none at all.
    """
    guard(cmd, allowed)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=max_line_bytes,
    )
    state = {"lines": 0, "truncated": False}

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # A single line longer than max_line_bytes: skip it, keep going.
                continue
            if not raw:
                return
            on_line(raw.decode(errors="replace").rstrip("\n"))
            state["lines"] += 1
            if state["lines"] >= max_lines:
                state["truncated"] = True
                return

    timed_out = False
    try:
        await asyncio.wait_for(pump(), timeout=timeout)
    except TimeoutError:
        timed_out = True

    stderr = b""
    if state["truncated"] or timed_out:
        await _terminate(proc)
    else:
        assert proc.stderr is not None
        stderr = await proc.stderr.read()
        await proc.wait()

    return ScanResult(
        lines_read=int(state["lines"]),
        truncated=bool(state["truncated"]),
        timed_out=timed_out,
        returncode=proc.returncode,
        stderr=stderr.decode(errors="replace"),
    )


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.kill()
    await proc.wait()
