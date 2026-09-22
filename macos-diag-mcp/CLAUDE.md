# macOS Diagnostics

Domain knowledge for diagnosing macOS UI problems (menu bar, hangs, wake
issues, runaway logging) on Martin's Macs. Derived from a real investigation
on macOS 27.0 (Build 26A428).

This knowledge ships as an MCP server in this folder — see
[The MCP server](#the-mcp-server) at the end for commands and architecture.

## Environment

- Main machine: Mac Studio M2 (Mac14,13), 64 GB, three displays on a CalDigit
  dock, Magic Keyboard with Touch ID.
- Second machine: MacBook Pro, used as a reference for comparisons.
- Touch ID is enabled for `sudo` via `pam_tid` (used from iTerm2).
- All UI names in English: the OS runs in English display language.

## Safety Rules

- Read-only by default. Never kill processes, delete files, or change
  settings without asking first.
- Never run `sudo` without asking. With `pam_tid` enabled, `sudo` opens a
  Touch ID prompt via `coreautha`. A prompt left pending can lock the menu bar
  (see Known Cases). Always cancel an unanswered `sudo` prompt.
- Never dump raw logs into the context. Every query must be bounded by a time
  window and piped through grouping (`uniq -c`) and `head`.
- Logs contain usernames, paths, hostnames, SSIDs and IP addresses. Summarize
  instead of quoting, and warn before anything is shared outside the machine.

## Triage Order

Work through these steps in order and stop as soon as the cause is clear.

1. Check for a pending modal state: authentication prompts and hung apps.
2. Establish the timeline: last sleep/wake events and the current boot time.
3. Check diagnostic reports within the time window.
4. Query the unified log for the relevant processes, grouped and normalized.
5. Compare with the MacBook Pro if the problem might be machine-specific.

## Step 1: Modal State

A menu bar that is entirely greyed out, including the Apple menu, means
macOS is in a modal state: a dialog waits for input, or the frontmost app is
hung. The dialog may be invisible (off-screen, on a sleeping display, or with
an invalid window after a display reconfiguration).

```bash
# Authentication / permission prompt processes
pgrep -lf 'coreautha|SecurityAgent|UserNotificationCenter|universalAccessAuthWarn'

# Is a Touch ID assertion being renewed? (every ~54 s while a prompt is open)
log show --last 10m --style compact \
  --predicate 'process == "loginwindow" AND eventMessage CONTAINS "TouchIDBlockScreenLock"' \
  | tail -n 5
```

To find who started an authentication session, look at its first log lines:

```bash
log show --last 5d --style compact --predicate 'processID == <PID>' | head -n 40
```

Hung apps cannot be detected reliably from the CLI. Ask Martin to check
Force Quit Applications (Cmd + Option + Esc).

## Step 2: Timeline

```bash
# Current boot time. Anchor the regex: a greedy '.*sec =' matches 'usec' instead.
BOOT_EPOCH=$(sysctl -n kern.boottime | sed -E 's/^\{ sec = ([0-9]+),.*/\1/')
date -r "$BOOT_EPOCH" "+%Y-%m-%d %H:%M:%S"

# Full wakes only ('DarkWake from' is excluded by the leading whitespace)
pmset -g log | grep -E "[[:space:]]Wake from" | tail -n 20

# Power settings
pmset -g
```

Use BSD `date` syntax on macOS: `date -r EPOCH` to format and
`date -j -f "%Y-%m-%d %H:%M:%S" "$TS" "+%s"` to parse.

## Step 3: Diagnostic Reports

Reports live in `~/Library/Logs/DiagnosticReports` and
`/Library/Logs/DiagnosticReports`.

```bash
find ~/Library/Logs/DiagnosticReports /Library/Logs/DiagnosticReports \
  -type f -newermt "YYYY-MM-DD HH:MM:SS" 2>/dev/null
```

- `.ips` files: the first line is a JSON header, the rest is a JSON body.
  Parse with `tail -n +2 file.ips | python3 -c 'import json,sys; ...'` and
  extract `procName`, `exception`, `termination`, and the frames of
  `faultingThread`.
- `ExcUserFault_*` with `is_simulated: 1` are simulated faults, not crashes.
- `JetsamEvent`: check which process has a `reason` (killed for memory).
- `.shutdownStall` and `.spin`/`.hang`: binary spindumps. Convert locally with
  `spindump -i <file>` (may require `sudo`, so ask first). The text output
  names the processes that blocked.
- Reports that also occur after a restart do not explain a state that a
  restart fixed. Use that to rule causes out.

## Step 4: Unified Log

Always use `--style compact` and a bounded window (`--last 1h` or
`--start "..." --end "..."`). The compact format is:

```text
2026-09-22 07:45:01.123 E  WindowServer[499:16f1] [subsystem:category] message
```

Field 4 is `process[pid:tid]`, so `awk '{print $4}'` gives the source.

Useful predicates:

```bash
# UI and session processes
'process IN {"WindowServer","loginwindow","ControlCenter","SystemUIServer","Dock","SecurityAgent","coreautha"}'

# Errors and faults only
'messageType == error OR messageType == fault'

# Hang hints
'eventMessage CONTAINS[c] "hang" OR eventMessage CONTAINS[c] "timed out"'
```

Group identical messages by normalizing volatile values:

```bash
normalize() {
  sed -E \
    -e 's/^[0-9-]+ [0-9:.]+ +//' \
    -e 's/\[[0-9]+:[0-9a-f]+\]//' \
    -e 's/[0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27}/<uuid>/g' \
    -e 's/0x[0-9a-fA-F]+/<hex>/g' \
    -e 's/[0-9]+/N/g'
}

log show --last 1h --style compact --predicate '<PREDICATE>' \
  | normalize | sort | uniq -c | sort -rn | head -n 40
```

Normalization hides identifiers and error codes. When a top message looks
relevant, fetch a few raw lines with `| head -n 5` to recover them.

To filter an existing log file to a window, compare the timestamp prefix:

```bash
awk -v from="$FROM" -v to="$TO" '{ ts = substr($0, 1, 19) } ts >= from && ts <= to' file.log
```

## Signals and What They Mean

| Signal | Meaning |
| --- | --- |
| `SACAssertScreenLockViaTouchIDBlocked` from `coreautha`, renewed every ~54 s | A Touch ID prompt is open, possibly invisible. Judge by the *last* assertion event, never by counting lines — see [Assertion counting](#assertion-counting) |
| `_CGXPackagesSetWindowConstraints: Invalid window` in large numbers | A window lost its valid placement, typical after display changes |
| `ActiveDisplayList count = 0` in loginwindow | No active display at that moment (displays asleep or re-enumerating) |
| `failed set_cursor_surface`, `timed out fence` | Display pipeline trouble around wake |
| `ExcUserFault` in `IconServices` `-[ISIconManager _init]` | Known macOS 27.0 background fault, occurs before and after restarts, not a cause |
| `artwork fetch failed for A.B.C` repeated in Music | Music retry loop for one item's artwork (see Known Cases) |

## Known Cases

### Menu bar greyed out after wake from deep sleep (2026-09-22)

- Symptom: whole menu bar greyed out, Apple menu unresponsive, no visible
  dialog, no hung app. Only a restart helped.
- Cause (strong evidence): a Touch ID `sudo` prompt from iTerm2 was pending
  before sleep. The `coreautha` session survived the wake, its window became
  invalid after the display reconfiguration, and the modal state stayed.
- Recovery without restart: quit `coreautha` in Activity Monitor, or press
  Ctrl + C in the iTerm2 tab with the waiting `sudo`.
- Prevention: do not leave `sudo` prompts pending. Keep `pam_tid.so` in
  `/etc/pam.d/sudo_local`. With iTerm2's "Allow sessions to survive logging
  out and back in" enabled, add `pam_reattach` above `pam_tid.so` or disable
  that option.

### Music artwork retry loop (2026-09-22)

- Symptom: ~471,000 `artwork fetch failed ... Code=9069` errors from Music in
  about two hours, always the same identifier.
- Identifier format `A.B.C`: convert the decimal parts to 16-digit uppercase
  hex (`python3 -c "print(format(N, '016X'))"`) and look them up as Music
  persistent IDs of tracks and playlists via AppleScript.
- Cause: the playlist "Ambient" had no cover. Setting a cover stopped the loop.
- Stream Deck's Apple Music plugin was suspected first but was not involved.
  Verify attribution by quitting the suspected app and re-checking the log.

## Lessons

- Verify assumptions with a direct test before recommending removal of
  software. Martin wants to keep tools he uses.
- Compare with the MacBook Pro early. A clean second machine separates local
  state from OS bugs.
- If a report or log shows the same pattern before and after a restart, it
  does not explain a problem that the restart fixed.
- For an Apple bug report, capture a sysdiagnose while the problem is active
  (Shift + Ctrl + Option + Cmd + .) and file it via Feedback Assistant.

---

## The MCP server

This document is also the source for `macos-diag-mcp`, which exposes the
playbook above as tools. The prose here is the reference; `knowledge.toml`
is the machine-readable copy that ships. **When a signal, case or rule
changes, change both** — `tests/test_knowledge.py` checks the TOML loads and
that the five triage steps survive, but nothing can detect the two drifting
apart in meaning.

### Commands

```bash
uv sync --extra dev          # set up
uv run pytest                # test
uv run ruff check . && uv run ruff format .
uv run macos-diag-mcp        # run the server (stdio)
```

Register it with a `uv`-managed stdio launch:

```json
{"command": "uv", "args": ["run", "--project", "<path>/macos-diag-mcp", "macos-diag-mcp"]}
```

### Tools

| Step | Tool | Purpose |
| --- | --- | --- |
| — | `diag_playbook` | Triage order, safety rules and which tool serves each step |
| — | `diag_signals` | What a log message means; pass a raw or normalized line |
| — | `diag_known_cases` | Investigations already solved, with cause and recovery |
| 1 | `check_modal_state` | Auth processes, and whether a Touch ID assertion is currently held |
| 1 | `auth_session_origin` | First log lines of a pid — who opened the session |
| 2 | `system_timeline` | Boot time, full wakes, sleeps, power settings |
| 3 | `list_diagnostic_reports` | Reports since a time (defaults to boot), classified |
| 3 | `read_diagnostic_report` | One `.ips`: exception, termination, faulting stack |
| 3 | `decode_spindump` | `spindump -i` on a binary report, without privileges |
| 4 | `log_summary` | Bounded window, grouped by normalized message |
| 4 | `log_sample` | A few raw lines, to recover what normalization hid |
| — | `music_persistent_ids` | Decode an `A.B.C` artwork identifier |

### Architecture

Tool bodies in `server.py` stay thin; the work is in modules that are
testable without a Mac:

- `shell.py` — the only place a subprocess starts. Holds the allow-list and
  the elevation refusal.
- `logs.py` — window parsing and caps, predicate building, normalization,
  streaming aggregation.
- `reports.py` — report discovery and `.ips` parsing (two JSON documents,
  not one).
- `timeline.py` — `kern.boottime` and `pmset -g log` parsing.
- `modal.py` — whether a Touch ID assertion is held (see below).
- `music.py` — `A.B.C` to Music persistent IDs.
- `redact.py` — masks identity in everything a tool returns.
- `config.py` / `config.toml` — limits, timeouts, paths, predicate presets.
- `knowledge.py` / `knowledge.toml` — the playbook, signals and cases.

Four of the safety rules are structural rather than advisory, so a future
tool cannot quietly break them:

- **Read-only, never elevated.** `shell.guard` refuses any binary outside
  `allowed_commands` and refuses `sudo`/`su`/`doas`/`osascript` outright.
  `decode_spindump` hands back the `sudo` command instead of running it.
  Arguments are deliberately *not* scanned for these words — searching the
  log *for* `sudo` is what hunting a stuck prompt looks like.
- **Every log query is bounded.** `logs.window_args` refuses a query with no
  window and one wider than `max_window_seconds`.
- **No raw log dumps.** `log_summary` only ever returns counts of normalized
  messages, and `log_sample` is capped at `max_sample_lines` and refuses to
  run against an unfiltered window.
- **Identity is masked on the way out.** `redact.Redactor` walks every result
  from a system tool before it is returned.

### Assertion counting

`check_modal_state`'s verdict is the highest-stakes output here — it is step 1,
and it tells you to stop and chase the prompt before anything else. It was wrong
once, in a way worth recording, because the same mistake is easy to reintroduce.

The original heuristic counted lines matching the Touch ID search and called two
or more an open prompt. On a live run that reported a prompt that was not there.
Two reasons, both visible in the log itself:

- loginwindow logs an assertion being **added** and being **cleared**, and both
  match the same search. A take followed by a release is ordinary authentication
  churn, not a waiting prompt.
- macOS repeats the method signature on every line of a multi-line message, so
  one release arrived as four lines, each carrying the `clear…` marker.

`modal.AssertionTracker` counts events rather than lines: it classifies each line
as a take or a release, checking the release markers **first** (a release message
quotes the assertion it releases, so a line matching both is a release),
collapses consecutive events sharing a timestamp, and reports `prompt_open` only
when the last event was a take. `tests/test_modal.py` pins the exact five lines
from the live false positive.

One consequence worth knowing: these messages are debug-level, which macOS keeps
in memory rather than on disk. They age out within minutes, so this tool answers
only about the live present — which is also the only time a stuck prompt matters.

### Redaction

Normalization in `logs.py` is a *grouping* mechanism and redacts nothing — it
collapses digits, hex and UUIDs so repeated messages count as one. A username,
home path, SSID or hostname passes straight through it. `redact.py` is the part
that masks, and it runs on the result of every system tool:

| Masked | Left alone |
| --- | --- |
| The user's home → `~` | Process names, subsystems, categories |
| Another user's home → `/Users/<user>` | System paths (`/System/...`, `/Library/...`) |
| Bare username (4+ chars) | Symbols, frames, error codes, exceptions |
| Hostnames and `*.local` | Timestamps, pids, tids |
| SSIDs, IPv4, IPv6, MAC, e-mail | Identifiers a tool exists to decode (`61.99.123`) |

Two properties worth keeping if this is ever changed:

- **The home path becomes `~`, not a placeholder.** `Path.expanduser()` resolves
  it, so a path from `list_diagnostic_reports` still works as the argument to
  `read_diagnostic_report`. A test asserts that round trip.
- **Errors are redacted unconditionally**, with no opt-out — a masked path
  still names the file that failed.

Every system tool takes `redact_result=false` for one call, when a path, host or
SSID is itself the clue. The result always carries a `privacy` key saying which
way it went. The knowledge tools have no such key: they return shipped text, not
machine data.

### Testing

`uv run pytest`. Subprocesses are faked only at the `shell` boundary
(`_run`/`_scan` in `tests/test_server.py`); parsing, grouping, capping,
redaction and classification all run for real against fixtures in
`tests/fakes.py`. `tests/test_redact.py` builds a redactor for a fixed fake
machine, so it does not depend on whose Mac runs it. The
shipped `knowledge.toml` is tested directly rather than through a fixture,
since it is the server's whole reason to exist.
