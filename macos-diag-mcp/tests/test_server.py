"""Tool-level tests. Only the subprocess boundary is faked; everything else is real."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from macos_diag_mcp import server
from macos_diag_mcp.shell import Completed, ScanResult
from tests.fakes import CRASH_BODY, JETSAM_BODY, SIMULATED_BODY, write_ips

BOOTTIME = "{ sec = 1758556800, usec = 123456 } Mon Sep 22 18:00:00 2026"
ARTWORK = (
    "2026-09-22 07:45:01.123 E  Music[812:1a2b] [com.apple.Music:artwork] "
    "artwork fetch failed for 61.99.123 Code=9069"
)
TOUCHID = (
    "2026-09-22 07:45:01.123 I  loginwindow[182:3ab1] [com.apple.loginwindow:default] "
    "SACAssertScreenLockViaTouchIDBlocked renewed"
)


def fake_scan(*line_batches: list[str], truncated: bool = False, stderr: str = ""):
    """Feed canned lines to whichever collector the tool passed in, call by call."""
    batches = list(line_batches)

    async def _scan(cmd, on_line, *, max_lines, timeout):
        lines = batches.pop(0) if batches else []
        for line in lines[:max_lines]:
            on_line(line)
        return ScanResult(len(lines), truncated, False, 0, stderr)

    return _scan


def completed(stdout: str = "", returncode: int = 0) -> Completed:
    return Completed(stdout, "", returncode, False)


def reports_in(directory):
    """Settings are frozen, so point them at a tmp dir by rebuilding them."""
    swapped = replace(server.settings.reports, directories=(directory,))
    return patch.object(server, "settings", replace(server.settings, reports=swapped))


# ── knowledge tools ───────────────────────────────────────────────────────────


def test_playbook_returns_the_steps_in_order_with_their_tools():
    result = server.diag_playbook()
    assert result["ok"]
    assert [s["number"] for s in result["steps"]] == [1, 2, 3, 4, 5]
    assert "check_modal_state" in result["steps"][0]["tools"]


def test_signals_explains_a_raw_log_line():
    result = server.diag_signals(ARTWORK)
    assert result["ok"]
    assert "music_persistent_ids" in result["matches"][0]["meaning"]


def test_signals_says_so_when_nothing_matches():
    result = server.diag_signals("zzzz quuxbar frobnicate")
    assert result["matches"] == []
    assert "no known signal" in result["note"]


def test_known_cases_finds_the_touch_id_investigation():
    result = server.diag_known_cases("menu bar")
    assert [c["name"] for c in result["cases"]] == [
        "Menu bar greyed out after wake from deep sleep"
    ]


# ── check_modal_state ─────────────────────────────────────────────────────────


async def test_modal_state_calls_out_an_open_touch_id_prompt():
    with (
        patch.object(
            server, "_run", AsyncMock(return_value=completed("701 coreautha\n"))
        ),
        patch.object(server, "_scan", fake_scan([TOUCHID] * 4)),
    ):
        result = await server.check_modal_state()

    assert result["ok"]
    assert result["auth_processes"] == [{"pid": 701, "command": "coreautha"}]
    assert result["touchid_assertions"]["prompt_open"] is True
    assert "grey out the whole menu bar" in result["verdict"]


async def test_modal_state_does_not_claim_a_prompt_that_was_released():
    """Regression: an add followed by a clear once reported a phantom prompt."""
    add = "2026-09-22 19:25:05.231 Df loginwindow[450:ddf2c] [x:y] -[LWTouchIDLockScreen addNewTouchIDBlockScreenLockAssertionForClient:withPID:] | returning: 0"
    clear = "2026-09-22 19:25:08.385 Df loginwindow[450:dbd0f] [x:y] -[LWTouchIDLockScreen clearTouchIDBlockScreenLockAssertionForClient:withPID:withDebounce:] | returning: 0"
    with (
        patch.object(
            server, "_run", AsyncMock(return_value=completed("701 coreautha\n"))
        ),
        patch.object(server, "_scan", fake_scan([add, clear, clear, clear])),
    ):
        result = await server.check_modal_state()

    assertions = result["touchid_assertions"]
    assert assertions["prompt_open"] is False
    assert assertions["last_event"] == "clear"
    assert (assertions["asserts"], assertions["clears"]) == (1, 1)
    assert "ordinary authentication churn" in result["verdict"]


async def test_modal_state_reports_no_activity_distinctly_from_a_release():
    with (
        patch.object(
            server, "_run", AsyncMock(return_value=completed("701 coreautha\n"))
        ),
        patch.object(server, "_scan", fake_scan([])),
    ):
        result = await server.check_modal_state()

    assert result["touchid_assertions"]["last_event"] == "none"
    assert "may be idle" in result["verdict"]


async def test_modal_state_handles_pgrep_finding_nothing():
    """pgrep exits 1 when nothing matches; that is an answer, not a failure."""
    with (
        patch.object(
            server, "_run", AsyncMock(return_value=completed("", returncode=1))
        ),
        patch.object(server, "_scan", fake_scan([])),
    ):
        result = await server.check_modal_state()

    assert result["ok"]
    assert result["auth_processes"] == []
    assert "hung frontmost app" in result["verdict"]


# ── auth_session_origin ───────────────────────────────────────────────────────


async def test_auth_session_origin_returns_the_opening_lines():
    with patch.object(server, "_scan", fake_scan([TOUCHID, ARTWORK])):
        result = await server.auth_session_origin(701)

    assert result["ok"]
    assert len(result["first_lines"]) == 2
    assert "Identity is masked" in result["privacy"]


async def test_auth_session_origin_rejects_a_bad_pid():
    result = await server.auth_session_origin(0)
    assert not result["ok"]


async def test_auth_session_origin_rejects_an_over_wide_window():
    result = await server.auth_session_origin(701, last="365d")
    assert not result["ok"]
    assert "cap" in result["error"]


# ── system_timeline ───────────────────────────────────────────────────────────


async def test_system_timeline_reports_boot_wakes_and_sleeps():
    wake = "2026-09-22 07:44:58 +0200 Wake \tWake from Deep Sleep [CDNVA] due to EC.LidOpen"
    dark = "2026-09-22 03:10:00 +0200 DarkWake \tDarkWake from Deep Sleep due to RTC"
    sleep = "2026-09-21 23:58:02 +0200 Sleep \tEntering Sleep state due to 'Idle Sleep'"

    with (
        patch.object(
            server,
            "_run",
            AsyncMock(side_effect=[completed(BOOTTIME), completed("SleepDisabled 0")]),
        ),
        patch.object(server, "_scan", fake_scan([sleep, dark, wake])),
    ):
        result = await server.system_timeline()

    assert result["boot_epoch"] == 1758556800
    assert len(result["wakes"]) == 1  # DarkWake excluded
    assert len(result["sleeps"]) == 1
    assert result["power_settings"] == "SleepDisabled 0"
    assert result["note"] == ""


async def test_system_timeline_explains_an_empty_wake_list():
    """A desktop that never slept must not look like a parse failure."""
    tally = "Sleep/Wakes since boot at 2026-09-18 20:03:34 +0200 :0"
    with (
        patch.object(
            server,
            "_run",
            AsyncMock(side_effect=[completed(BOOTTIME), completed("standby 0")]),
        ),
        patch.object(server, "_scan", fake_scan([tally])),
    ):
        result = await server.system_timeline()

    assert result["wakes"] == []
    assert result["sleep_wake_tally"] == tally
    assert "has not slept since boot" in result["note"]


async def test_system_timeline_reports_a_failed_sysctl():
    with patch.object(server, "_run", AsyncMock(return_value=completed("junk"))):
        result = await server.system_timeline()
    assert not result["ok"]


async def test_system_timeline_rejects_an_out_of_range_count():
    result = await server.system_timeline(wake_count=5000)
    assert not result["ok"]


# ── diagnostic reports ────────────────────────────────────────────────────────


async def test_list_reports_defaults_the_window_to_the_boot_time(tmp_path):
    write_ips(tmp_path, "Music.ips", CRASH_BODY)
    with (
        reports_in(tmp_path),
        patch.object(server, "_run", AsyncMock(return_value=completed(BOOTTIME))),
    ):
        result = await server.list_diagnostic_reports()

    assert result["since_source"] == "current boot time"
    assert result["reports"][0]["kind"] == "crash"


async def test_list_reports_honours_an_explicit_since(tmp_path):
    write_ips(tmp_path, "JetsamEvent-1.ips", JETSAM_BODY)
    with reports_in(tmp_path):
        result = await server.list_diagnostic_reports(since="2000-01-01 00:00:00")

    assert result["since_source"] == "argument"
    assert "Safari (per-process-limit)" in result["reports"][0]["note"]


async def test_list_reports_rejects_an_unreadable_since():
    result = await server.list_diagnostic_reports(since="yesterday")
    assert not result["ok"]


def test_read_report_returns_the_faulting_stack(tmp_path):
    path = write_ips(tmp_path, "Music.ips", CRASH_BODY)
    result = server.read_diagnostic_report(str(path))

    assert result["process"] == "Music"
    assert result["exception"]["signal"] == "SIGABRT"
    assert result["faulting_frames"][0].startswith("libsystem_kernel.dylib")


def test_read_report_flags_a_simulated_fault_as_not_a_crash(tmp_path):
    path = write_ips(tmp_path, "ExcUserFault_IconServices.ips", SIMULATED_BODY)
    result = server.read_diagnostic_report(str(path))

    assert result["is_simulated"] is True
    assert "not a crash" in result["note"]


def test_read_report_sends_a_spindump_to_the_right_tool(tmp_path):
    path = tmp_path / "WindowServer.shutdownStall"
    path.write_bytes(b"\x00binary")
    result = server.read_diagnostic_report(str(path))

    assert not result["ok"]
    assert "decode_spindump" in result["error"]


def test_read_report_reports_a_missing_file():
    assert not server.read_diagnostic_report("/nope/missing.ips")["ok"]


async def test_decode_spindump_returns_the_head(tmp_path):
    path = tmp_path / "WindowServer.shutdownStall"
    path.write_bytes(b"\x00binary")
    with patch.object(
        server, "_scan", fake_scan(["Date/Time: 2026-09-22", "Process: WindowServer"])
    ):
        result = await server.decode_spindump(str(path))

    assert result["ok"]
    assert len(result["head"]) == 2


async def test_decode_spindump_hands_back_the_sudo_command_rather_than_running_it(
    tmp_path,
):
    path = tmp_path / "WindowServer.shutdownStall"
    path.write_bytes(b"\x00binary")
    with patch.object(server, "_scan", fake_scan([], stderr="spindump requires root")):
        result = await server.decode_spindump(str(path))

    assert not result["ok"]
    assert result["run_yourself"].startswith("sudo spindump -i ")
    assert result["run_yourself"].endswith("WindowServer.shutdownStall")
    assert "pending prompt can lock the menu bar" in result["warning"]


# ── log tools ─────────────────────────────────────────────────────────────────


async def test_log_summary_groups_a_retry_loop_into_one_counted_line():
    flood = [ARTWORK.replace("07:45:01", f"07:45:{i % 60:02d}") for i in range(471)]
    with patch.object(server, "_scan", fake_scan(flood)):
        result = await server.log_summary(last="2h", processes="Music")

    assert result["lines_matched"] == 471
    assert result["distinct_messages"] == 1
    assert result["top_messages"][0]["count"] == 471
    assert result["top_processes"][0] == {"count": 471, "process": "Music"}
    assert result["predicate"] == '(process IN {"Music"})'


async def test_log_summary_defaults_to_the_configured_window():
    with patch.object(server, "_scan", fake_scan([ARTWORK])):
        result = await server.log_summary(preset="errors")
    assert result["window"] == {"last": server.settings.log.default_window}


async def test_log_summary_refuses_a_window_beyond_the_cap():
    result = await server.log_summary(last="90d")
    assert not result["ok"]
    assert "cap" in result["error"]


async def test_log_summary_rejects_an_unknown_preset():
    result = await server.log_summary(last="1h", preset="everything")
    assert not result["ok"]
    assert "unknown preset" in result["error"]


async def test_log_summary_warns_when_the_scan_was_cut_short():
    with patch.object(server, "_scan", fake_scan([ARTWORK] * 3, truncated=True)):
        result = await server.log_summary(last="1h")
    assert any("stopped after" in n for n in result["notes"])


async def test_log_sample_returns_raw_lines_with_their_identifiers_intact():
    with patch.object(server, "_scan", fake_scan([ARTWORK])):
        result = await server.log_sample(last="1h", processes="Music", lines=3)

    assert "61.99.123" in result["lines"][0]
    assert "Code=9069" in result["lines"][0]


async def test_log_sample_refuses_an_unfiltered_window():
    """It hands back unnormalized lines, so it will not run without a filter."""
    with patch.object(server, "_scan", fake_scan([ARTWORK])):
        result = await server.log_sample(last="1h")

    assert not result["ok"]
    assert "log_summary first" in result["error"]


async def test_log_sample_caps_the_line_count():
    result = await server.log_sample(last="1h", processes="Music", lines=500)
    assert not result["ok"]


# ── music_persistent_ids ──────────────────────────────────────────────────────


def test_music_persistent_ids_decodes_an_identifier_from_a_log_line():
    result = server.music_persistent_ids("61.99.123")
    assert result["ok"]
    assert result["parts"][2]["persistent_id"] == "000000000000007B"


def test_music_persistent_ids_rejects_junk():
    assert not server.music_persistent_ids("not-an-id")["ok"]


# ── registered tools ──────────────────────────────────────────────────────────


EXPECTED_TOOLS = {
    "diag_playbook",
    "diag_signals",
    "diag_known_cases",
    "check_modal_state",
    "auth_session_origin",
    "system_timeline",
    "list_diagnostic_reports",
    "read_diagnostic_report",
    "decode_spindump",
    "log_summary",
    "log_sample",
    "music_persistent_ids",
}


async def test_the_registered_tool_names_do_not_drift():
    names = {t.name for t in await server.mcp.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_every_tool_documents_itself():
    for tool in await server.mcp.list_tools():
        assert tool.description, f"{tool.name} has no docstring"


@pytest.mark.parametrize("binary", ["pkill", "rm", "defaults", "killall", "sudo"])
def test_no_mutating_binary_is_reachable(binary):
    assert binary not in server.settings.shell.allowed_commands


# ── redaction at the tool boundary ────────────────────────────────────────────

# A fake machine's identity, not this one's: a test fixture must not carry a
# real username or SSID into the repository — which is the point of the feature
# under test. `fake_machine` makes the redactor believe in this fixture's user.
LEAKY = (
    "2026-09-22 07:45:01.123 E  sharingd[812:1a2b] [x:y] user testuser at "
    '/Users/testuser/Documents joined SSID "Test-WLAN" from 192.168.1.42'
)


def fake_machine():
    """Point the redactor at the fixture's identity instead of the real one."""
    return patch.object(
        server,
        "redactor",
        replace(
            server.redactor,
            home="/Users/testuser",
            username="testuser",
            hostname="Test-Studio.local",
        ),
    )


async def test_log_sample_masks_identity_by_default():
    with fake_machine(), patch.object(server, "_scan", fake_scan([LEAKY])):
        result = await server.log_sample(last="1h", processes="sharingd")

    line = result["lines"][0]
    assert "testuser" not in line
    assert "Test-WLAN" not in line
    assert "192.168.1.42" not in line
    assert "sharingd" in line  # the diagnosis survives
    assert "Identity is masked" in result["privacy"]


async def test_log_sample_returns_raw_identity_when_asked():
    with fake_machine(), patch.object(server, "_scan", fake_scan([LEAKY])):
        result = await server.log_sample(
            last="1h", processes="sharingd", redact_result=False
        )

    assert "testuser" in result["lines"][0]
    assert "Redaction is OFF" in result["privacy"]


async def test_log_summary_masks_identity_too():
    """The tool most likely to be called first must not be the one that leaks."""
    with fake_machine(), patch.object(server, "_scan", fake_scan([LEAKY])):
        result = await server.log_summary(last="1h", processes="sharingd")

    assert "testuser" not in result["top_messages"][0]["message"]
    assert "Identity is masked" in result["privacy"]


async def test_report_paths_come_back_masked_but_still_usable(tmp_path, monkeypatch):
    """A masked path is worthless if the next tool call cannot resolve it."""
    home = tmp_path / "Users" / "someone"
    (home / "Reports").mkdir(parents=True)
    monkeypatch.setattr(server, "redactor", replace(server.redactor, home=str(home)))
    path = write_ips(home / "Reports", "Music.ips", CRASH_BODY)

    with reports_in(home / "Reports"):
        result = await server.list_diagnostic_reports(since="2000-01-01 00:00:00")

    masked = result["reports"][0]["path"]
    assert masked.startswith("~/")
    assert str(home) not in masked
    assert Path(masked.replace("~", str(home))).is_file()
    assert path.is_file()


async def test_decode_spindump_masks_the_command_it_hands_back(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "someone"
    home.mkdir(parents=True)
    monkeypatch.setattr(server, "redactor", replace(server.redactor, home=str(home)))
    report = home / "WindowServer.shutdownStall"
    report.write_bytes(b"\x00binary")

    with patch.object(server, "_scan", fake_scan([], stderr="requires root")):
        result = await server.decode_spindump(str(report))

    assert result["run_yourself"] == "sudo spindump -i ~/WindowServer.shutdownStall"


def test_errors_are_redacted_even_without_the_opt_in(monkeypatch):
    monkeypatch.setattr(
        server, "redactor", replace(server.redactor, home="/Users/someone")
    )
    result = server.read_diagnostic_report("/Users/someone/missing.ips")
    assert not result["ok"]
    assert "/Users/someone" not in result["error"]
    assert "missing.ips" in result["error"]  # still names the file


async def test_every_system_tool_reports_which_way_redaction_went():
    with (
        patch.object(
            server, "_run", AsyncMock(return_value=completed("701 coreautha"))
        ),
        patch.object(server, "_scan", fake_scan([])),
    ):
        assert "privacy" in await server.check_modal_state()
        assert "privacy" in await server.auth_session_origin(701)


def test_knowledge_tools_stay_free_of_the_privacy_key():
    """They return shipped text, not machine data — a privacy line would be noise."""
    assert "privacy" not in server.diag_playbook()
    assert "privacy" not in server.diag_signals("x")
