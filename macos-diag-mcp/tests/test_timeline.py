import pytest

from macos_diag_mcp.timeline import (
    PowerLogCollector,
    TimelineError,
    parse_boot_epoch,
    parse_power_event,
)

BOOTTIME = "{ sec = 1758556800, usec = 123456 } Mon Sep 22 18:00:00 2026"

FULL_WAKE = (
    "2026-09-22 07:44:58 +0200 Wake                \tWake from Deep Sleep [CDNVA] "
    "due to EC.LidOpen/Lid Open: Using AC"
)
DARK_WAKE = "2026-09-22 03:10:00 +0200 DarkWake            \tDarkWake from Deep Sleep [CDNVA] due to RTC"
SLEEP = (
    "2026-09-21 23:58:02 +0200 Sleep               \tEntering Sleep state due to "
    "'Idle Sleep': Using AC"
)


def test_parse_boot_epoch_reads_sec_not_usec():
    """The whole point of anchoring: a greedy match returns 123456 instead."""
    assert parse_boot_epoch(BOOTTIME) == 1758556800


def test_parse_boot_epoch_tolerates_surrounding_whitespace():
    assert parse_boot_epoch(f"  {BOOTTIME}\n") == 1758556800


def test_parse_boot_epoch_rejects_unexpected_output():
    with pytest.raises(TimelineError):
        parse_boot_epoch("kern.boottime: unavailable")


def test_parse_power_event_recognises_a_full_wake():
    event = parse_power_event(FULL_WAKE)
    assert event is not None
    assert event.kind == "wake"
    assert event.timestamp == "2026-09-22 07:44:58"


def test_parse_power_event_excludes_darkwake():
    """DarkWake is background maintenance and would drown out the real wakes."""
    assert parse_power_event(DARK_WAKE) is None


def test_parse_power_event_recognises_a_sleep():
    event = parse_power_event(SLEEP)
    assert event is not None
    assert event.kind == "sleep"


def test_parse_power_event_ignores_other_lines():
    assert (
        parse_power_event("2026-09-22 07:44:58 +0200 Assertions \tPID 123 Created")
        is None
    )
    assert parse_power_event("Total Sleep/Wakes since boot: 4") is None


def test_collector_keeps_only_the_tail():
    collector = PowerLogCollector(2)
    for hour in range(5):
        collector.feed(FULL_WAKE.replace("07:44:58", f"0{hour}:44:58"))
    assert len(collector.events) == 2
    assert collector.events[-1].timestamp.endswith("04:44:58")


def test_collector_separates_sleeps_from_wakes():
    collector = PowerLogCollector(10)
    collector.feed(SLEEP)
    collector.feed(FULL_WAKE)
    collector.feed(DARK_WAKE)
    assert len(collector.by_kind("wake")) == 1
    assert len(collector.by_kind("sleep")) == 1


def test_collector_counts_every_line_it_saw():
    collector = PowerLogCollector(10)
    for line in (SLEEP, DARK_WAKE, "noise"):
        collector.feed(line)
    assert collector.scanned == 3


def test_collector_captures_pmsets_own_sleep_wake_tally():
    collector = PowerLogCollector(10)
    collector.feed(
        "Sleep/Wakes since boot at 2026-09-18 20:03:34 +0200 :0   Dark Wake Count in this sleep cycle:0"
    )
    assert collector.tally.endswith("sleep cycle:0")
    assert collector.events == []


def test_the_tally_line_is_not_mistaken_for_an_event():
    collector = PowerLogCollector(10)
    collector.feed("Sleep/Wakes since boot at 2026-09-18 20:03:34 +0200 :0")
    assert collector.by_kind("wake") == []
