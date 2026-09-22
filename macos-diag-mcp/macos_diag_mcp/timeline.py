"""Boot time and sleep/wake history — the anchors every other query is bounded by."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime

# `sysctl -n kern.boottime` prints:
#   { sec = 1758556800, usec = 123456 } Mon Sep 22 18:00:00 2026
# Anchored deliberately: a greedy `.*sec = ` matches the `usec` field instead
# and hands back a microsecond count as an epoch.
_BOOTTIME = re.compile(r"^\{\s*sec\s*=\s*(\d+)")

# A leading whitespace before "Wake from" is what separates a full wake from a
# "DarkWake from", which is background maintenance and not a user-visible wake.
_FULL_WAKE = re.compile(r"\sWake from")
_SLEEP = re.compile(r"Entering Sleep state")

_PMSET_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# pmset's own tally, e.g. 'Sleep/Wakes since boot at <time> :0'. It is the
# difference between 'this machine never slept' and 'the parser missed them'.
_TALLY = re.compile(r"^\s*Sleep/Wakes since boot")


class TimelineError(ValueError):
    """A system command returned something this module cannot read."""


def parse_boot_epoch(sysctl_output: str) -> int:
    match = _BOOTTIME.match(sysctl_output.strip())
    if not match:
        raise TimelineError(
            f"unexpected kern.boottime output: {sysctl_output.strip()!r}"
        )
    return int(match.group(1))


def format_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class PowerEvent:
    kind: str
    timestamp: str
    detail: str


def parse_power_event(line: str) -> PowerEvent | None:
    """Classify one `pmset -g log` line as a full wake, a sleep, or neither."""
    stamp = _PMSET_LINE.match(line)
    if not stamp:
        return None
    if _FULL_WAKE.search(line):
        kind = "wake"
    elif _SLEEP.search(line):
        kind = "sleep"
    else:
        return None
    return PowerEvent(kind=kind, timestamp=stamp.group(1), detail=line.strip())


class PowerLogCollector:
    """Keeps the last `limit` sleep/wake events while `pmset -g log` streams past.

    The log holds weeks of entries and only the tail matters, so it is never
    held whole.
    """

    def __init__(self, limit: int) -> None:
        self._events: deque[PowerEvent] = deque(maxlen=limit)
        self.scanned = 0
        self.tally = ""

    def feed(self, line: str) -> None:
        self.scanned += 1
        if _TALLY.match(line):
            self.tally = line.strip()
            return
        event = parse_power_event(line)
        if event is not None:
            self._events.append(event)

    @property
    def events(self) -> list[PowerEvent]:
        return list(self._events)

    def by_kind(self, kind: str) -> list[PowerEvent]:
        return [e for e in self._events if e.kind == kind]
