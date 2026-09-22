import os
import time
from datetime import datetime, timedelta

import pytest

from macos_diag_mcp.reports import (
    ReportError,
    faulting_frames,
    find_reports,
    jetsam_victims,
    parse_ips,
    parse_since,
)
from tests.fakes import CRASH_BODY, JETSAM_BODY, SIMULATED_BODY, write_ips

SPINDUMP_SUFFIXES = (".shutdownStall", ".spin", ".hang", ".stackshot")


# ── parse_since ───────────────────────────────────────────────────────────────


def test_parse_since_reads_the_playbooks_format():
    assert parse_since("2026-09-22 07:45:01") == datetime(2026, 9, 22, 7, 45, 1)


def test_parse_since_reads_a_bare_date():
    assert parse_since("2026-09-22") == datetime(2026, 9, 22)


def test_parse_since_reads_an_epoch():
    assert parse_since("1758556800") == datetime.fromtimestamp(1758556800)


def test_parse_since_rejects_prose():
    with pytest.raises(ValueError, match="could not read"):
        parse_since("last tuesday")


# ── parse_ips ─────────────────────────────────────────────────────────────────


def test_parse_ips_splits_the_header_line_from_the_body(tmp_path):
    path = write_ips(tmp_path, "Music.ips", CRASH_BODY)
    header, body = parse_ips(path)
    assert header["bug_type"] == "309"
    assert body["procName"] == "Music"


def test_parse_ips_rejects_a_single_line_file(tmp_path):
    path = tmp_path / "broken.ips"
    path.write_text('{"app_name": "Music"}')
    with pytest.raises(ReportError, match="no body"):
        parse_ips(path)


def test_parse_ips_rejects_invalid_json(tmp_path):
    path = tmp_path / "broken.ips"
    path.write_text('{"app_name": "Music"}\nnot json at all')
    with pytest.raises(ReportError, match="not a readable"):
        parse_ips(path)


# ── faulting_frames ───────────────────────────────────────────────────────────


def test_faulting_frames_follows_the_faulting_thread_index():
    frames = faulting_frames(CRASH_BODY, 10)
    assert "not_the_faulting_thread" not in " ".join(frames)
    assert frames[0] == "libsystem_kernel.dylib  __pthread_kill + 8"


def test_faulting_frames_names_the_image_of_each_frame():
    assert "IconServices  -[ISIconManager _init] + 120" in faulting_frames(
        CRASH_BODY, 10
    )


def test_faulting_frames_falls_back_to_an_offset_without_a_symbol():
    assert faulting_frames(CRASH_BODY, 10)[2] == "?  <4242>"


def test_faulting_frames_honours_the_limit():
    assert len(faulting_frames(CRASH_BODY, 2)) == 2


def test_faulting_frames_is_empty_when_there_is_no_faulting_thread():
    assert faulting_frames({"procName": "Music"}, 10) == []


def test_faulting_frames_survives_an_out_of_range_index():
    assert faulting_frames({"faultingThread": 7, "threads": []}, 10) == []


# ── jetsam_victims ────────────────────────────────────────────────────────────


def test_jetsam_victims_returns_only_processes_with_a_reason():
    assert jetsam_victims(JETSAM_BODY) == [
        {"name": "Safari", "reason": "per-process-limit"}
    ]


def test_jetsam_victims_is_empty_for_a_crash_report():
    assert jetsam_victims(CRASH_BODY) == []


# ── find_reports ──────────────────────────────────────────────────────────────


def test_find_reports_excludes_anything_older_than_the_cutoff(tmp_path):
    old = write_ips(tmp_path, "Old.ips", CRASH_BODY)
    write_ips(tmp_path, "New.ips", CRASH_BODY)
    stale = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(old, (stale, stale))

    found = find_reports(
        (tmp_path,), datetime.now() - timedelta(hours=1), SPINDUMP_SUFFIXES
    )
    assert [r.path.name for r in found] == ["New.ips"]


def test_find_reports_classifies_a_simulated_fault(tmp_path):
    write_ips(tmp_path, "ExcUserFault_IconServices.ips", SIMULATED_BODY)
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.kind == "simulated-fault"
    assert "not a crash" in report.note


def test_find_reports_classifies_a_real_user_fault(tmp_path):
    body = dict(SIMULATED_BODY)
    del body["is_simulated"]
    write_ips(tmp_path, "ExcUserFault_Music.ips", body)
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.kind == "user-fault"


def test_find_reports_classifies_a_crash(tmp_path):
    write_ips(tmp_path, "Music-2026-09-22.ips", CRASH_BODY)
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.kind == "crash"
    assert report.process == "Music"


def test_find_reports_names_the_jetsam_victim(tmp_path):
    write_ips(tmp_path, "JetsamEvent-2026-09-22.ips", JETSAM_BODY)
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.kind == "jetsam"
    assert "Safari (per-process-limit)" in report.note


def test_find_reports_flags_a_binary_spindump_without_parsing_it(tmp_path):
    (tmp_path / "WindowServer_2026-09-22.shutdownStall").write_bytes(b"\x00\x01binary")
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.kind == "spindump"
    assert report.process == "WindowServer"
    assert "decode_spindump" in report.note


def test_find_reports_marks_an_unreadable_ips_instead_of_raising(tmp_path):
    (tmp_path / "garbage.ips").write_text("not json\nnot json either")
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.note == "unreadable"


def test_find_reports_skips_a_directory_it_cannot_read(tmp_path):
    write_ips(tmp_path, "Music.ips", CRASH_BODY)
    found = find_reports(
        (tmp_path, tmp_path / "does-not-exist"),
        datetime.fromtimestamp(0),
        SPINDUMP_SUFFIXES,
    )
    assert len(found) == 1


def test_find_reports_sorts_newest_first(tmp_path):
    first = write_ips(tmp_path, "First.ips", CRASH_BODY)
    time.sleep(0.01)
    write_ips(tmp_path, "Second.ips", CRASH_BODY)
    older = datetime.now().timestamp() - 60
    os.utime(first, (older, older))

    found = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)
    assert [r.path.name for r in found] == ["Second.ips", "First.ips"]


def test_find_reports_names_a_non_crash_file_rather_than_leaving_it_blank(tmp_path):
    (tmp_path / "SFA-sos.json_2026-09-22-140426_Mac-Studio-M2.diag").write_text("{}")
    report = find_reports((tmp_path,), datetime.fromtimestamp(0), SPINDUMP_SUFFIXES)[0]
    assert report.kind == "other"
    assert report.process == "SFA"
    assert "not a crash report (.diag)" in report.note
