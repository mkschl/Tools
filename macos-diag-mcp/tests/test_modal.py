"""Assertion tracking. The regression here is a real false positive from a live run."""

import pytest

from macos_diag_mcp.config import load_settings
from macos_diag_mcp.modal import AssertionTracker

MODAL = load_settings().modal

# Verbatim from a live check_modal_state call that wrongly reported an open
# prompt: one assertion taken, then released, plus three continuation lines of
# the release message. Counting lines gave 5 "renewals"; the answer is zero.
LIVE_LINES = [
    "2026-09-22 19:25:05.231 Df loginwindow[450:ddf2c] [x:Standard] -[LWTouchIDLockScreen addNewTouchIDBlockScreenLockAssertionForClient:withPID:] | returning: 0",
    "2026-09-22 19:25:08.385 Df loginwindow[450:dbd0f] [x:Standard] -[LWTouchIDLockScreen clearTouchIDBlockScreenLockAssertionForClient:withPID:withDebounce:] | clearTouchIDBlockScreenLockAssertionForClient: coreautha, with PID: 12080",
    "2026-09-22 19:25:08.385 Df loginwindow[450:dbd0f] [x:Standard] -[LWTouchIDLockScreen clearTouchIDBlockScreenLockAssertionForClient:withPID:withDebounce:] | assertion timeout set to 3.000000 seconds from now",
    "2026-09-22 19:25:08.385 Df loginwindow[450:dbd0f] [x:Standard] -[LWTouchIDLockScreen clearTouchIDBlockScreenLockAssertionForClient:withPID:withDebounce:] | current assertions: {",
    "2026-09-22 19:25:08.385 Df loginwindow[450:dbd0f] [x:Standard] -[LWTouchIDLockScreen clearTouchIDBlockScreenLockAssertionForClient:withPID:withDebounce:] | returning: 0",
]
RENEWAL = (
    "2026-09-22 07:45:01.123 I  coreautha[701:2f1a] [x:default] "
    "SACAssertScreenLockViaTouchIDBlocked renewed"
)


@pytest.fixture
def tracker() -> AssertionTracker:
    return AssertionTracker(MODAL.touchid_assert_markers, MODAL.touchid_clear_markers)


def test_an_add_followed_by_a_clear_is_not_an_open_prompt(tracker):
    """The live false positive: five matching lines, no prompt waiting."""
    for line in LIVE_LINES:
        tracker.feed(line)
    assert tracker.prompt_open is False
    assert tracker.last == "clear"


def test_continuation_lines_are_not_counted_as_events(tracker):
    for line in LIVE_LINES:
        tracker.feed(line)
    assert tracker.asserts == 1
    assert (
        tracker.clears == 1
    )  # four clear-prefixed lines at one timestamp, one release
    assert tracker.events == 2


def test_an_unreleased_assertion_is_an_open_prompt(tracker):
    tracker.feed(LIVE_LINES[0])
    assert tracker.prompt_open is True
    assert tracker.last == "assert"


def test_a_reassertion_after_a_release_reopens_it(tracker):
    tracker.feed(LIVE_LINES[0])
    tracker.feed(LIVE_LINES[1])
    tracker.feed(LIVE_LINES[0])
    assert tracker.prompt_open is True
    assert (tracker.asserts, tracker.clears) == (2, 1)


def test_coreauthas_renewal_marker_counts_as_an_assertion(tracker):
    tracker.feed(RENEWAL)
    assert tracker.prompt_open is True
    assert tracker.asserts == 1


def test_a_clear_naming_the_assertion_is_read_as_a_clear(tracker):
    """The release message quotes what it releases; it must not read as an add."""
    both = (
        "clearTouchIDBlockScreenLockAssertionForClient: releasing "
        "addNewTouchIDBlockScreenLockAssertion held by coreautha"
    )
    tracker.feed(both)
    assert tracker.last == "clear"
    assert tracker.asserts == 0


def test_nothing_at_all_is_not_an_open_prompt(tracker):
    assert tracker.prompt_open is False
    assert tracker.last == ""
    assert tracker.events == 0


def test_unrelated_lines_are_ignored(tracker):
    tracker.feed("2026-09-22 07:45:01.123 E  WindowServer[1:2] something else entirely")
    assert tracker.events == 0


def test_two_real_clears_at_different_times_both_count(tracker):
    """Collapsing is per-timestamp, so distinct events are never merged away."""
    tracker.feed(LIVE_LINES[1])
    tracker.feed(LIVE_LINES[1].replace("19:25:08.385", "19:26:11.100"))
    assert tracker.clears == 2
