"""MCP tools for diagnosing macOS UI problems, read-only and bounded.

Tool bodies stay thin — the work lives in shell/logs/reports/timeline. Expected
failures come back as error-shaped dicts rather than exceptions, so the calling
model can react instead of losing the turn.

Three of the playbook's safety rules are structural here rather than advisory:
`shell` refuses anything outside a read-only allow-list and refuses sudo
outright, every log query must name a bounded window, and results come back
grouped and capped so a runaway logger cannot fill the context.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import structlog
from mcp.server.fastmcp import FastMCP

from macos_diag_mcp import logs, modal, music, redact, reports, timeline
from macos_diag_mcp.config import load_settings
from macos_diag_mcp.knowledge import load_knowledge, match_cases, match_signals
from macos_diag_mcp.shell import CommandRefused, run, scan

mcp = FastMCP("macos-diag")
settings = load_settings()
knowledge = load_knowledge()
log = structlog.get_logger()
redactor = redact.build(settings.redact)

REDACTED_NOTE = (
    "Identity is masked: home paths as ~, plus usernames, hostnames, SSIDs, IP and MAC "
    "addresses and e-mail. Process names, system paths, symbols and error codes are "
    "untouched. Pass redact=false for one call if a masked value is itself the clue."
)
RAW_WARNING = (
    "Redaction is OFF for this call. The result can carry usernames, home paths, "
    "hostnames, SSIDs and IP addresses. Summarize rather than quoting, and warn before "
    "any of it leaves this machine."
)
NORMALIZATION_NOTE = (
    "Counts are of normalized messages: identifiers, addresses and numbers were "
    "replaced, so distinct error codes group together. Use log_sample with the same "
    "predicate to recover a few raw lines once a message looks relevant."
)


def _error(message: str, **context: object) -> dict:
    """Errors are always redacted: a masked path still names the file that failed."""
    log.warning("tool_error", message=message, **context)
    return {"ok": False, "error": redactor.text(message)}


def _finish(result: dict, redact_result: bool) -> dict:
    """Mask identity in a system tool's result, and say which way it went."""
    if redact_result and redactor.enabled:
        return redactor.mapping(result) | {"privacy": REDACTED_NOTE}
    return result | {"privacy": RAW_WARNING}


async def _scan(
    cmd: list[str], on_line: Callable[[str], None], *, max_lines: int, timeout: float
):
    return await scan(
        cmd,
        allowed=settings.shell.allowed_commands,
        timeout=timeout,
        max_lines=max_lines,
        max_line_bytes=settings.shell.max_line_bytes,
        on_line=on_line,
    )


async def _run(cmd: list[str], timeout: float | None = None):
    return await run(
        cmd,
        allowed=settings.shell.allowed_commands,
        timeout=timeout or settings.shell.default_timeout_seconds,
    )


def _query(
    last: str, start: str, end: str, preset: str, processes: str, predicate: str
) -> tuple[list[str], str, dict]:
    """Build a `log show` command, its predicate and a description of its window."""
    window = logs.window_args(
        last or ("" if (start or end) else settings.log.default_window),
        start,
        end,
        settings.log.max_window_seconds,
    )
    combined = logs.build_predicate(predicate, preset, processes, settings.log.presets)
    described = (
        {"start": start, "end": end}
        if start or end
        else {"last": last or settings.log.default_window}
    )
    return logs.show_command(combined, window), combined, described


def _scan_notes(result, subject: str) -> list[str]:
    notes = []
    if result.truncated:
        notes.append(
            f"stopped after {result.lines_read} lines — narrow the window or the "
            f"predicate; the {subject} shown are from the scanned portion only"
        )
    if result.timed_out:
        notes.append("the command timed out; results are partial")
    if result.stderr.strip():
        notes.append(f"stderr: {result.stderr.strip()[:400]}")
    return notes


# ── Knowledge ─────────────────────────────────────────────────────────────────


@mcp.tool()
def diag_playbook() -> dict:
    """The triage order and safety rules for a macOS UI problem. Start here.

    Returns the five steps in the order they are meant to be worked, with the
    tool for each, plus the rules that bound what this server will do. Stop as
    soon as the cause is clear — the later steps cost time and context.
    """
    return {
        "ok": True,
        "summary": knowledge.summary,
        "safety_rules": list(knowledge.safety_rules),
        "steps": [
            {
                "number": s.number,
                "title": s.title,
                "detail": s.detail,
                "tools": list(s.tools),
            }
            for s in knowledge.steps
        ],
        "lessons": list(knowledge.lessons),
    }


@mcp.tool()
def diag_signals(query: str = "") -> dict:
    """What a log message means, from the known-signals table.

    Pass a log line — raw or normalized — to get the signals that match it, best
    first. An empty query returns the whole table. A signal explains what the
    message indicates, not that it is the cause: several of these are background
    noise that appears on a healthy machine too.
    """
    matched = match_signals(knowledge.signals, query)
    return {
        "ok": True,
        "query": query,
        "matches": [
            {"pattern": s.pattern, "process": s.process, "meaning": s.meaning}
            for s in matched
        ],
        "note": (
            "no known signal matches; this may still be noise — check whether it "
            "also appears outside the problem window"
            if not matched
            else "a match is a lead, not a verdict"
        ),
    }


@mcp.tool()
def diag_known_cases(query: str = "") -> dict:
    """Investigations already solved on this machine, with cause and recovery.

    Check this before digging: two of the problems seen so far look like an OS
    fault and are not. An empty query returns every case.
    """
    matched = match_cases(knowledge.cases, query)
    return {
        "ok": True,
        "query": query,
        "cases": [
            {
                "name": c.name,
                "date": c.date,
                "symptom": c.symptom,
                "cause": c.cause,
                "recovery": c.recovery,
                "prevention": c.prevention,
            }
            for c in matched
        ],
    }


# ── Step 1: modal state ───────────────────────────────────────────────────────


@mcp.tool()
async def check_modal_state(redact_result: bool = True) -> dict:
    """Step 1: is macOS waiting on a dialog that may be invisible?

    A menu bar greyed out down to the Apple menu means a modal state: a prompt
    is waiting, or the frontmost app is hung. The prompt can be invisible —
    off-screen, on a sleeping display, or holding a window that a display change
    invalidated.

    Reports the running authentication processes and whether a Touch ID
    screen-lock assertion is currently held — an assertion taken with no release
    after it. Adds and releases both show up in the log and a single message can
    span several lines, so the counts are of events, not of matching lines.

    Run this while the symptom is live. These messages are debug-level, which
    macOS keeps in memory rather than on disk, so they age out within minutes.

    Hung apps cannot be detected from the CLI: ask Martin to check Force Quit
    Applications (Cmd + Option + Esc).
    """
    pattern = "|".join(settings.modal.auth_processes)
    try:
        found = await _run(["pgrep", "-lf", pattern])
        tail = logs.Tailer(settings.modal.touchid_sample_lines)
        tracker = modal.AssertionTracker(
            settings.modal.touchid_assert_markers,
            settings.modal.touchid_clear_markers,
        )

        def observe(line: str) -> None:
            tail.feed(line)
            tracker.feed(line)

        cmd, _, _ = _query(
            settings.modal.touchid_window,
            "",
            "",
            "",
            "",
            settings.modal.touchid_predicate,
        )
        result = await _scan(
            cmd,
            observe,
            max_lines=settings.log.max_scanned_lines,
            timeout=settings.shell.default_timeout_seconds,
        )
    except CommandRefused as exc:
        return _error(str(exc))

    # pgrep exits 1 when nothing matches, which is an answer rather than a failure.
    processes = []
    for line in found.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            processes.append({"pid": int(pid), "command": command})

    payload = {
        "ok": True,
        "auth_processes": processes,
        "touchid_assertions": {
            "window": settings.modal.touchid_window,
            "asserts": tracker.asserts,
            "clears": tracker.clears,
            "last_event": tracker.last or "none",
            "renewal_interval_seconds": settings.modal.touchid_renewal_seconds,
            "prompt_open": tracker.prompt_open,
            "lines_seen": tail.seen,
            "recent": tail.lines,
        },
        "verdict": _modal_verdict(processes, tracker),
        "next": [
            "auth_session_origin(pid=...) on any process above, to find who opened it",
            "ask Martin to check Force Quit Applications (Cmd + Option + Esc) for a hung app",
        ],
        "notes": _scan_notes(result, "renewals"),
    }
    return _finish(payload, redact_result)


def _modal_verdict(processes: list[dict], tracker: modal.AssertionTracker) -> str:
    if tracker.prompt_open:
        return (
            "A Touch ID assertion is held and nothing has released it: a prompt is open. "
            "This alone can grey out the whole menu bar, and the prompt may be invisible. "
            "Find its origin before anything else — see diag_known_cases('Touch ID')."
        )
    if tracker.events:
        return (
            f"Touch ID activity in the window ({tracker.asserts} taken, {tracker.clears} "
            "released) but the last event was a release, so no prompt is waiting. This is "
            "ordinary authentication churn, not a stuck prompt."
        )
    if processes:
        return (
            "Authentication processes are running but no assertion was taken in the "
            "window. They may be idle; check when each one started with auth_session_origin."
        )
    return "No authentication prompt found. If the menu bar is still frozen, suspect a hung frontmost app."


@mcp.tool()
async def auth_session_origin(
    pid: int, last: str = "5d", redact_result: bool = True
) -> dict:
    """Who started an authentication session: the first log lines of a process.

    Point this at a pid from check_modal_state. The opening lines name the
    calling process — an iTerm2 `sudo` is the case seen so far. The window is
    generous on purpose: a session can be days older than the symptom.

    Identity is masked; pass redact_result=false if the calling user is the point.
    """
    if pid <= 0:
        return _error("pid must be positive")
    try:
        sampler = logs.Sampler(settings.log.auth_origin_lines)
        cmd, _, window = _query(last, "", "", "", "", f"processID == {pid}")
        result = await _scan(
            cmd,
            sampler.feed,
            max_lines=settings.log.max_scanned_lines,
            timeout=settings.shell.default_timeout_seconds,
        )
    except (logs.WindowError, ValueError, CommandRefused) as exc:
        return _error(str(exc), pid=pid)

    payload = {
        "ok": True,
        "pid": pid,
        "window": window,
        "first_lines": sampler.lines,
        "note": ""
        if sampler.lines
        else "nothing logged for this pid in the window — it may predate it",
        "notes": _scan_notes(result, "lines"),
    }
    return _finish(payload, redact_result)


# ── Step 2: timeline ──────────────────────────────────────────────────────────


@mcp.tool()
async def system_timeline(wake_count: int = 0, redact_result: bool = True) -> dict:
    """Step 2: boot time, recent sleeps and full wakes, and the power settings.

    Everything after this should be bounded by these times. Only full wakes are
    listed — DarkWake entries are background maintenance and drown out the ones
    a user would notice.
    """
    count = wake_count or settings.timeline.default_wake_count
    if not 0 < count <= settings.timeline.max_wake_count:
        return _error(
            f"wake_count must be between 1 and {settings.timeline.max_wake_count}"
        )

    try:
        boot = await _run(["sysctl", "-n", "kern.boottime"])
        if not boot.ok:
            return _error(f"could not read kern.boottime: {boot.stderr.strip()}")
        epoch = timeline.parse_boot_epoch(boot.stdout)

        collector = timeline.PowerLogCollector(count)
        scanned = await _scan(
            ["pmset", "-g", "log"],
            collector.feed,
            max_lines=settings.log.max_scanned_lines,
            timeout=settings.shell.default_timeout_seconds,
        )
        power = await _run(["pmset", "-g"])
    except (timeline.TimelineError, CommandRefused) as exc:
        return _error(str(exc))

    payload = {
        "ok": True,
        "boot_time": timeline.format_epoch(epoch),
        "boot_epoch": epoch,
        "wakes": [
            {"timestamp": e.timestamp, "detail": e.detail}
            for e in collector.by_kind("wake")
        ],
        "sleeps": [
            {"timestamp": e.timestamp, "detail": e.detail}
            for e in collector.by_kind("sleep")
        ],
        "sleep_wake_tally": collector.tally,
        "power_settings": power.stdout.strip(),
        "note": _timeline_note(collector),
        "notes": _scan_notes(scanned, "events"),
    }
    return _finish(payload, redact_result)


def _timeline_note(collector: timeline.PowerLogCollector) -> str:
    """An empty wake list is an answer; say so, so it is not read as a failure."""
    if collector.events:
        return ""
    return (
        "No sleep or wake entries in the retained power log — a machine that has not "
        "slept since boot, or a rotated store. Check sleep_wake_tally, and anchor "
        "later queries to boot_time instead of a wake."
    )


# ── Step 3: diagnostic reports ────────────────────────────────────────────────


@mcp.tool()
async def list_diagnostic_reports(
    since: str = "", limit: int = 0, redact_result: bool = True
) -> dict:
    """Step 3: crash, fault, jetsam and spindump reports written since a time.

    `since` takes 'YYYY-MM-DD HH:MM:SS' or an epoch; left empty it uses the
    current boot time. Each entry is classified, so a simulated fault and a real
    crash are not weighed the same.

    A report that also appears after a restart does not explain a state the
    restart fixed — check the timestamps against system_timeline before
    following one.

    Home paths come back as `~`, which stays valid as the argument to
    read_diagnostic_report and decode_spindump.
    """
    count = limit or settings.reports.default_limit
    if not 0 < count <= settings.reports.max_limit:
        return _error(f"limit must be between 1 and {settings.reports.max_limit}")

    try:
        if since:
            cutoff = reports.parse_since(since)
        else:
            boot = await _run(["sysctl", "-n", "kern.boottime"])
            if not boot.ok:
                return _error(f"could not read kern.boottime: {boot.stderr.strip()}")
            cutoff = datetime.fromtimestamp(timeline.parse_boot_epoch(boot.stdout))
        found = reports.find_reports(
            settings.reports.directories, cutoff, settings.reports.spindump_suffixes
        )
    except (ValueError, timeline.TimelineError, CommandRefused) as exc:
        return _error(str(exc), since=since)

    payload = {
        "ok": True,
        "since": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "since_source": "argument" if since else "current boot time",
        "total": len(found),
        "reports": [
            {
                "path": str(r.path),
                "modified": r.modified.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": r.kind,
                "process": r.process,
                "note": r.note,
            }
            for r in found[:count]
        ],
        "truncated": len(found) > count,
    }
    return _finish(payload, redact_result)


@mcp.tool()
def read_diagnostic_report(path: str, redact_result: bool = True) -> dict:
    """One .ips report: what crashed, how it ended, and the faulting stack.

    Returns the process, exception, termination and the top frames of the
    faulting thread, plus the killed processes for a JetsamEvent. For a binary
    spindump (.shutdownStall, .spin, .hang) use decode_spindump instead.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        return _error(f"no such report: {path}")
    if file.suffix in settings.reports.spindump_suffixes:
        return _error(f"{file.name} is a binary spindump — use decode_spindump")

    try:
        header, body = reports.parse_ips(file)
    except (OSError, reports.ReportError) as exc:
        return _error(str(exc), path=path)

    simulated = bool(body.get("is_simulated"))
    payload = {
        "ok": True,
        "path": str(file),
        "process": body.get("procName") or header.get("app_name"),
        "bug_type": header.get("bug_type"),
        "timestamp": header.get("timestamp"),
        "os_version": header.get("os_version"),
        "is_simulated": simulated,
        "exception": body.get("exception"),
        "termination": body.get("termination"),
        "jetsam_victims": reports.jetsam_victims(body),
        "faulting_frames": reports.faulting_frames(body, settings.reports.max_frames),
        "note": (
            "is_simulated: a reported fault, not a crash — it did not take the process down"
            if simulated
            else ""
        ),
    }
    return _finish(payload, redact_result)


@mcp.tool()
async def decode_spindump(path: str, redact_result: bool = True) -> dict:
    """Decode a binary spindump report; its head names the processes that blocked.

    Tries `spindump -i` without privileges. Some reports can only be read as
    root — this server never runs sudo, so in that case it hands back the exact
    command for Martin to run himself. He should cancel any Touch ID prompt he
    does not intend to answer.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        return _error(f"no such report: {path}")

    captured: list[str] = []

    def take(line: str) -> None:
        if len(captured) < settings.reports.spindump_head_lines:
            captured.append(line)

    try:
        result = await _scan(
            ["spindump", "-i", str(file)],
            take,
            max_lines=settings.reports.spindump_head_lines,
            timeout=settings.shell.spindump_timeout_seconds,
        )
    except CommandRefused as exc:
        return _error(str(exc), path=path)

    manual = f"sudo spindump -i {file}"
    if not captured:
        payload = {
            "ok": False,
            "error": "spindump produced no output; it likely needs privileges",
            "stderr": result.stderr.strip()[:400],
            "run_yourself": manual,
            "warning": "Answer or cancel the Touch ID prompt promptly — a pending prompt can lock the menu bar.",
        }
        return _finish(payload, redact_result)

    payload = {
        "ok": True,
        "path": str(file),
        "head": captured,
        "truncated": result.truncated,
        "run_yourself": manual if result.stderr.strip() else "",
        "note": "The blocked processes are named near the top of a spindump.",
    }
    return _finish(payload, redact_result)


# ── Step 4: unified log ───────────────────────────────────────────────────────


@mcp.tool()
async def log_summary(
    last: str = "",
    start: str = "",
    end: str = "",
    preset: str = "",
    processes: str = "",
    predicate: str = "",
    limit: int = 0,
    redact_result: bool = True,
) -> dict:
    """Step 4: the unified log over a window, grouped by normalized message.

    A window is required: `last` as 30s/10m/1h/5d, or `start` and `end` as
    'YYYY-MM-DD HH:MM:SS'. Narrow with any combination of:
    - preset: ui_processes, errors, hang_hints
    - processes: comma-separated names, e.g. "WindowServer,Music"
    - predicate: a raw NSPredicate, ANDed with the rest

    Comes back as counts per message shape and per process, so a process in a
    retry loop stands out immediately. Raw lines are never returned here.

    Identity is masked; pass redact_result=false if a home path or host is the clue.
    """
    count = limit or settings.log.default_group_limit
    if not 0 < count <= settings.log.max_group_limit:
        return _error(f"limit must be between 1 and {settings.log.max_group_limit}")

    aggregator = logs.Aggregator()
    try:
        cmd, combined, window = _query(last, start, end, preset, processes, predicate)
        result = await _scan(
            cmd,
            aggregator.feed,
            max_lines=settings.log.max_scanned_lines,
            timeout=settings.shell.default_timeout_seconds,
        )
    except (logs.WindowError, ValueError, CommandRefused) as exc:
        return _error(str(exc))

    if aggregator.matched == 0 and result.stderr.strip():
        return _error(
            f"log show failed: {result.stderr.strip()[:400]}", predicate=combined
        )

    payload = {
        "ok": True,
        "window": window,
        "predicate": combined or "(none — every message in the window)",
        "lines_matched": aggregator.matched,
        "distinct_messages": len(aggregator.messages),
        "top_messages": aggregator.top_messages(count),
        "top_processes": aggregator.top_processes(count),
        "note": NORMALIZATION_NOTE,
        "notes": _scan_notes(result, "counts"),
    }
    return _finish(payload, redact_result)


@mcp.tool()
async def log_sample(
    last: str = "",
    start: str = "",
    end: str = "",
    preset: str = "",
    processes: str = "",
    predicate: str = "",
    lines: int = 0,
    redact_result: bool = True,
) -> dict:
    """A handful of raw log lines, to recover what normalization hid.

    Use this after log_summary, with a predicate narrowed to the one message
    that looks relevant — the identifiers and error codes it returns are exactly
    what grouping replaced. A predicate, preset or process list is required:
    this tool hands back unnormalized lines and will not do so for an
    unfiltered window.

    Identity is masked. Normalization in log_summary is grouping, not redaction —
    this is where masking matters most. Pass redact_result=false only when the
    masked value is itself what you are chasing.
    """
    count = lines or settings.log.default_sample_lines
    if not 0 < count <= settings.log.max_sample_lines:
        return _error(f"lines must be between 1 and {settings.log.max_sample_lines}")

    sampler = logs.Sampler(count)
    try:
        cmd, combined, window = _query(last, start, end, preset, processes, predicate)
        if not combined:
            return _error(
                "log_sample needs a predicate, preset or process list — it returns raw "
                "lines, so it will not run against an unfiltered window. Use log_summary first."
            )
        result = await _scan(
            cmd,
            sampler.feed,
            max_lines=settings.log.max_scanned_lines,
            timeout=settings.shell.default_timeout_seconds,
        )
    except (logs.WindowError, ValueError, CommandRefused) as exc:
        return _error(str(exc))

    if not sampler.lines and result.stderr.strip():
        return _error(
            f"log show failed: {result.stderr.strip()[:400]}", predicate=combined
        )

    payload = {
        "ok": True,
        "window": window,
        "predicate": combined,
        "lines": sampler.lines,
        "notes": _scan_notes(result, "lines"),
    }
    return _finish(payload, redact_result)


# ── Case-specific helpers ─────────────────────────────────────────────────────


@mcp.tool()
def music_persistent_ids(identifier: str) -> dict:
    """Decode a Music `A.B.C` artwork identifier into Music persistent IDs.

    From `artwork fetch failed for A.B.C` in the log. The decimal parts are
    persistent IDs, which Music and AppleScript only show as 16-digit uppercase
    hex. Run the returned AppleScript to name the track and playlist — in the
    case seen so far the culprit was a playlist with no cover, retried forever.
    """
    try:
        parts = music.persistent_ids(identifier)
    except ValueError as exc:
        return _error(str(exc), identifier=identifier)
    return {
        "ok": True,
        "identifier": identifier,
        "parts": parts,
        "note": (
            "Roles are inferred from position — confirm by running each lookup. "
            "A part that resolves to nothing is likely the database ID."
        ),
    }


def configure_logging() -> None:
    """Log to stderr only — stdout carries the JSON-RPC stream."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def main() -> None:
    configure_logging()
    if sys.platform != "darwin":
        log.warning(
            "not_macos",
            platform=sys.platform,
            hint="every tool here shells out to macOS-only commands",
        )
    mcp.run()


if __name__ == "__main__":
    main()
