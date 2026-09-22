"""Is a Touch ID screen-lock assertion actually being held right now?

Counting log lines cannot answer this, which is the trap this module exists to
avoid. loginwindow logs an assertion being *added* and, separately, being
*cleared*, both matching the same search; and one log message can span several
lines, so a single add/clear pair can look like a dozen events. What decides it
is whether the most recent assertion event was an add that no clear followed.

The messages involved are debug-level, which macOS keeps in memory rather than
persisting. They age out within minutes, so this is only ever a question about
the live present — which is also when a stuck prompt actually matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ASSERT = "assert"
CLEAR = "clear"

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+")


@dataclass
class AssertionTracker:
    """Follows assertion add/clear events as the log streams past."""

    assert_markers: tuple[str, ...]
    clear_markers: tuple[str, ...]
    asserts: int = 0
    clears: int = 0
    last: str = ""
    _last_key: tuple[str, str] = field(default=("", ""))

    def feed(self, line: str) -> None:
        # Clear first: a clear message names the assertion it is clearing, so a
        # line matching both markers is a release, not another add.
        if any(marker in line for marker in self.clear_markers):
            kind = CLEAR
        elif any(marker in line for marker in self.assert_markers):
            kind = ASSERT
        else:
            # A continuation fragment carrying neither marker.
            return

        stamp = _TIMESTAMP.match(line)
        key = (stamp.group(0) if stamp else "", kind)
        self.last = kind
        # macOS repeats the method signature on every line of a multi-line
        # message, so the same event arrives several times at one timestamp.
        # Counting those would inflate the tally without changing the verdict.
        if key == self._last_key:
            return
        self._last_key = key

        if kind == CLEAR:
            self.clears += 1
        else:
            self.asserts += 1

    @property
    def prompt_open(self) -> bool:
        """True only when an assertion was taken and nothing released it."""
        return self.last == ASSERT

    @property
    def events(self) -> int:
        return self.asserts + self.clears
