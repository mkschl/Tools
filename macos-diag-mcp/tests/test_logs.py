import pytest

from macos_diag_mcp.logs import (
    Aggregator,
    Sampler,
    Tailer,
    WindowError,
    build_predicate,
    normalize,
    show_command,
    source_process,
    window_args,
    window_seconds,
)

COMPACT = (
    "2026-09-22 07:45:01.123 E  WindowServer[499:16f1] "
    "[com.apple.windowserver:default] _CGXPackagesSetWindowConstraints: Invalid window 0x2a"
)
PRESETS = {
    "errors": "messageType == error",
    "hang_hints": 'eventMessage CONTAINS "hang"',
}


# ── normalize ─────────────────────────────────────────────────────────────────


def test_normalize_drops_the_timestamp():
    assert not normalize(COMPACT).startswith("2026")


def test_normalize_drops_pid_and_tid():
    assert "[499:16f1]" not in normalize(COMPACT)
    assert "WindowServer" in normalize(COMPACT)


def test_normalize_replaces_hex_before_digits_eat_it():
    assert "<hex>" in normalize("something at 0xdeadbeef")


def test_normalize_replaces_uuids():
    line = "session 1F2E3D4C-5B6A-7988-9A0B-1C2D3E4F5A6B ended"
    assert "<uuid>" in normalize(line)


def test_normalize_collapses_numbers():
    assert normalize("retry 17 of 400") == "retry N of N"


def test_normalize_groups_lines_that_differ_only_in_volatile_values():
    a = "2026-09-22 07:45:01.123 E  Music[812:1a2b] artwork fetch failed for 61.99.123 Code=9069"
    b = "2026-09-22 09:12:44.907 E  Music[812:9f3c] artwork fetch failed for 61.99.123 Code=9069"
    assert normalize(a) == normalize(b)


# ── source_process ────────────────────────────────────────────────────────────


def test_source_process_reads_field_four():
    assert source_process(COMPACT) == "WindowServer"


def test_source_process_tolerates_a_short_line():
    assert source_process("2026-09-22 07:45:01.123 E") == ""


# ── windows ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "seconds"), [("30s", 30), ("10m", 600), ("1h", 3600), ("5d", 432000)]
)
def test_window_seconds_understands_each_unit(text, seconds):
    assert window_seconds(text) == seconds


@pytest.mark.parametrize("text", ["1 hour", "h1", "", "1w", "-1h"])
def test_window_seconds_rejects_junk(text):
    with pytest.raises(WindowError):
        window_seconds(text)


def test_window_args_uses_last():
    assert window_args("1h", "", "", 604800) == ["--last", "1h"]


def test_window_args_uses_an_explicit_range():
    args = window_args("", "2026-09-22 07:00:00", "2026-09-22 08:00:00", 604800)
    assert args == ["--start", "2026-09-22 07:00:00", "--end", "2026-09-22 08:00:00"]


def test_window_args_requires_a_window():
    with pytest.raises(WindowError, match="required"):
        window_args("", "", "", 604800)


def test_window_args_rejects_a_half_specified_range():
    with pytest.raises(WindowError, match="both start and end"):
        window_args("", "2026-09-22 07:00:00", "", 604800)


def test_window_args_rejects_mixing_last_with_a_range():
    with pytest.raises(WindowError, match="not both"):
        window_args("1h", "2026-09-22 07:00:00", "2026-09-22 08:00:00", 604800)


def test_window_args_enforces_the_cap():
    with pytest.raises(WindowError, match="cap"):
        window_args("30d", "", "", 604800)


# ── predicates ────────────────────────────────────────────────────────────────


def test_build_predicate_expands_a_preset():
    assert build_predicate("", "errors", "", PRESETS) == "(messageType == error)"


def test_build_predicate_rejects_an_unknown_preset():
    with pytest.raises(ValueError, match="unknown preset"):
        build_predicate("", "nope", "", PRESETS)


def test_build_predicate_turns_a_process_list_into_an_in_clause():
    built = build_predicate("", "", "WindowServer, Music", PRESETS)
    assert built == '(process IN {"WindowServer","Music"})'


def test_build_predicate_ands_every_part_together():
    built = build_predicate("x == 1", "errors", "Music", PRESETS)
    assert built == '(messageType == error) AND (process IN {"Music"}) AND (x == 1)'


def test_build_predicate_is_empty_when_nothing_is_given():
    assert build_predicate("", "", "", PRESETS) == ""


def test_show_command_omits_the_predicate_flag_when_there_is_none():
    assert show_command("", ["--last", "1h"]) == [
        "log",
        "show",
        "--style",
        "compact",
        "--last",
        "1h",
    ]


def test_show_command_includes_the_predicate():
    cmd = show_command("messageType == error", ["--last", "1h"])
    assert cmd[-2:] == ["--predicate", "messageType == error"]


# ── Aggregator ────────────────────────────────────────────────────────────────


def test_aggregator_skips_log_shows_own_header_lines():
    agg = Aggregator()
    agg.feed('Filtering the log data using "messageType == 16"')
    agg.feed("Timestamp                       Type Activity             PID")
    assert agg.matched == 0


def test_aggregator_counts_repeats_as_one_group():
    agg = Aggregator()
    for i in range(500):
        agg.feed(COMPACT.replace("07:45:01", f"07:45:{i % 60:02d}"))
    assert agg.matched == 500
    assert len(agg.messages) == 1
    assert agg.top_messages(5)[0]["count"] == 500


def test_aggregator_attributes_lines_to_processes():
    agg = Aggregator()
    agg.feed(COMPACT)
    agg.feed(COMPACT.replace("WindowServer[499:16f1]", "Music[812:1a2b]"))
    assert agg.top_processes(5)[0]["count"] == 1
    assert {p["process"] for p in agg.top_processes(5)} == {"WindowServer", "Music"}


def test_aggregator_top_messages_honours_the_limit():
    agg = Aggregator()
    for i in range(10):
        agg.feed(COMPACT.replace("Invalid window", f"Problem {chr(97 + i)}"))
    assert len(agg.top_messages(3)) == 3


# ── Sampler and Tailer ────────────────────────────────────────────────────────


def test_sampler_keeps_the_first_lines_only():
    sampler = Sampler(2)
    for i in range(10):
        sampler.feed(COMPACT.replace("window", f"window{i}"))
    assert len(sampler.lines) == 2
    assert "window0" in sampler.lines[0]
    assert sampler.full


def test_sampler_ignores_header_lines():
    sampler = Sampler(3)
    sampler.feed("Filtering the log data using ...")
    assert sampler.lines == []


def test_tailer_keeps_the_last_lines_and_counts_all_of_them():
    tailer = Tailer(2)
    for i in range(10):
        tailer.feed(COMPACT.replace("window", f"window{i}"))
    assert tailer.seen == 10
    assert len(tailer.lines) == 2
    assert "window9" in tailer.lines[-1]
