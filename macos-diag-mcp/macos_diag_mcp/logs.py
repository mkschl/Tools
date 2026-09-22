"""Unified-log querying: bounded windows, normalized grouping, capped samples.

This is the Python equivalent of the `log show | sed | sort | uniq -c | head`
pipeline from the playbook, with the shell taken out of it. Lines are
normalized and counted as they stream past, so a two-hour window containing
half a million identical errors costs a Counter entry rather than a context
window.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, field

# `log show --style compact`:
#   2026-09-22 07:45:01.123 E  WindowServer[499:16f1] [subsystem:category] message
# Anything not starting with a date is log's own header or filtering notice.
DATA_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

_TIMESTAMP = re.compile(r"^[0-9-]+ [0-9:.]+ +")
_PID_TID = re.compile(r"\[[0-9]+:[0-9a-f]+\]")
_UUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27}")
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_DIGITS = re.compile(r"[0-9]+")

_WINDOW = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class WindowError(ValueError):
    """The requested time window is malformed or wider than the configured cap."""


def normalize(line: str) -> str:
    """Collapse a compact log line to its shape, dropping volatile values.

    Order matters: UUIDs and hex would otherwise be eaten by the digit rule and
    stop grouping usefully. What survives is the message with every identifier,
    address and number replaced — which is what makes 471,000 artwork failures
    show up as one line with a count instead of 471,000 lines.
    """
    out = _TIMESTAMP.sub("", line)
    out = _PID_TID.sub("", out)
    out = _UUID.sub("<uuid>", out)
    out = _HEX.sub("<hex>", out)
    return _DIGITS.sub("N", out)


def source_process(line: str) -> str:
    """The `process` of `process[pid:tid]`, field 4 of a compact line."""
    fields = line.split(maxsplit=4)
    if len(fields) < 4:
        return ""
    return fields[3].split("[", 1)[0]


def window_seconds(last: str) -> int:
    match = _WINDOW.match(last.strip())
    if not match:
        raise WindowError(f"window must look like 30s, 10m, 1h or 5d, got {last!r}")
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def window_args(last: str, start: str, end: str, max_seconds: int) -> list[str]:
    """`log show` window flags, refusing an unbounded or over-wide query.

    A window is mandatory: without one `log show` walks the whole store, which
    takes minutes and says nothing a narrower window would not have said first.
    """
    if start or end:
        if not (start and end):
            raise WindowError("an explicit window needs both start and end")
        if last:
            raise WindowError("pass either last, or start and end — not both")
        return ["--start", start, "--end", end]
    if not last:
        raise WindowError("a time window is required")
    seconds = window_seconds(last)
    if seconds > max_seconds:
        raise WindowError(
            f"window {last} exceeds the {max_seconds}s cap; narrow it, or anchor "
            "it to the boot or wake time from system_timeline"
        )
    return ["--last", last]


def build_predicate(
    predicate: str, preset: str, processes: str, presets: dict[str, str]
) -> str:
    """Combine a preset, a process list and a free-form predicate with AND."""
    parts: list[str] = []
    if preset:
        if preset not in presets:
            raise ValueError(
                f"unknown preset {preset!r}; available: {', '.join(sorted(presets))}"
            )
        parts.append(presets[preset])
    names = [p.strip() for p in processes.replace(";", ",").split(",") if p.strip()]
    if names:
        quoted = ",".join(f'"{n}"' for n in names)
        parts.append(f"process IN {{{quoted}}}")
    if predicate:
        parts.append(predicate)
    return " AND ".join(f"({p})" for p in parts)


def show_command(predicate: str, window: list[str]) -> list[str]:
    cmd = ["log", "show", "--style", "compact", *window]
    if predicate:
        cmd += ["--predicate", predicate]
    return cmd


@dataclass
class Aggregator:
    """Counts normalized messages and source processes as lines stream past."""

    messages: Counter[str] = field(default_factory=Counter)
    processes: Counter[str] = field(default_factory=Counter)
    matched: int = 0

    def feed(self, line: str) -> None:
        if not DATA_LINE.match(line):
            return
        self.matched += 1
        self.messages[normalize(line)] += 1
        process = source_process(line)
        if process:
            self.processes[process] += 1

    def top_messages(self, limit: int) -> list[dict[str, object]]:
        return [
            {"count": count, "message": message}
            for message, count in self.messages.most_common(limit)
        ]

    def top_processes(self, limit: int) -> list[dict[str, object]]:
        return [
            {"count": count, "process": process}
            for process, count in self.processes.most_common(limit)
        ]


@dataclass
class Sampler:
    """Keeps the first N raw data lines and drops the rest."""

    limit: int
    lines: list[str] = field(default_factory=list)

    def feed(self, line: str) -> None:
        if len(self.lines) < self.limit and DATA_LINE.match(line):
            self.lines.append(line)

    @property
    def full(self) -> bool:
        return len(self.lines) >= self.limit


@dataclass
class Tailer:
    """Keeps the last N raw data lines and drops the rest.

    The tail is what matters for an assertion that is renewed on a timer: the
    most recent renewals say whether it is still being held.
    """

    limit: int
    _lines: deque[str] = field(default_factory=lambda: deque(maxlen=1))
    seen: int = 0

    def __post_init__(self) -> None:
        self._lines = deque(maxlen=max(self.limit, 1))

    def feed(self, line: str) -> None:
        if DATA_LINE.match(line):
            self.seen += 1
            self._lines.append(line)

    @property
    def lines(self) -> list[str]:
        return list(self._lines)
