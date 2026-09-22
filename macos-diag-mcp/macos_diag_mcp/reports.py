"""Diagnostic reports: finding them in a window and reading what they say.

An `.ips` file is two JSON documents — a one-line header, then the body — which
is why it cannot be handed straight to `json.load`. Everything here is
read-only; nothing is moved, rewritten or deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


class ReportError(ValueError):
    """The file is not a diagnostic report this module can read."""


def parse_since(value: str) -> datetime:
    """Accept the playbook's `YYYY-MM-DD HH:MM:SS`, an ISO time, or an epoch."""
    text = value.strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text))
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"could not read {value!r} as a time; use 'YYYY-MM-DD HH:MM:SS' or an epoch"
    )


@dataclass(frozen=True)
class ReportSummary:
    path: Path
    modified: datetime
    kind: str
    process: str
    note: str


def find_reports(
    directories: tuple[Path, ...], since: datetime, spindump_suffixes: tuple[str, ...]
) -> list[ReportSummary]:
    """Reports modified at or after `since`, newest first.

    A directory that is unreadable is skipped rather than fatal: the
    system-wide one needs privileges the user's own does not.
    """
    cutoff = since.timestamp()
    found: list[ReportSummary] = []
    for directory in directories:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file() or entry.stat().st_mtime < cutoff:
                    continue
                modified = datetime.fromtimestamp(entry.stat().st_mtime)
            except OSError:
                continue
            found.append(_summarize(entry, modified, spindump_suffixes))
    found.sort(key=lambda r: r.modified, reverse=True)
    return found


def _summarize(
    path: Path, modified: datetime, spindump_suffixes: tuple[str, ...]
) -> ReportSummary:
    if path.suffix in spindump_suffixes:
        return ReportSummary(
            path=path,
            modified=modified,
            kind="spindump",
            process=path.stem.split("_", 1)[0],
            note="binary spindump — decode it with decode_spindump to see which processes blocked",
        )
    if path.suffix != ".ips":
        # .diag and friends: analytics payloads, not crash reports.
        return ReportSummary(
            path,
            modified,
            "other",
            path.stem.split("_", 1)[0].split("-", 1)[0],
            f"not a crash report ({path.suffix or 'no extension'})",
        )

    try:
        header, body = parse_ips(path)
    except (OSError, ReportError):
        return ReportSummary(path, modified, "ips", "", "unreadable")

    process = str(body.get("procName") or header.get("app_name") or "")
    kind = _ips_kind(path, body)
    return ReportSummary(path, modified, kind, process, _note(kind, body))


def _ips_kind(path: Path, body: dict) -> str:
    name = path.name
    if name.startswith("JetsamEvent"):
        return "jetsam"
    if name.startswith("ExcUserFault"):
        return "simulated-fault" if _is_simulated(body) else "user-fault"
    return "crash" if body.get("exception") else "ips"


def _is_simulated(body: dict) -> bool:
    return bool(body.get("is_simulated"))


def _note(kind: str, body: dict) -> str:
    if kind == "simulated-fault":
        return "is_simulated: a reported fault, not a crash"
    if kind == "jetsam":
        victims = jetsam_victims(body)
        if victims:
            named = ", ".join(f"{v['name']} ({v['reason']})" for v in victims[:3])
            return f"killed for memory: {named}"
        return "memory-pressure event with no process carrying a reason"
    return ""


def parse_ips(path: Path) -> tuple[dict, dict]:
    """Split an .ips into its JSON header line and its JSON body."""
    text = path.read_text(errors="replace")
    head, sep, rest = text.partition("\n")
    if not sep:
        raise ReportError(f"{path.name} has no body after its header line")
    try:
        header = json.loads(head)
        body = json.loads(rest)
    except json.JSONDecodeError as exc:
        raise ReportError(f"{path.name} is not a readable .ips: {exc}") from exc
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise ReportError(f"{path.name} does not hold two JSON objects")
    return header, body


def jetsam_victims(body: dict) -> list[dict[str, str]]:
    """Processes a JetsamEvent killed — the ones carrying a `reason`."""
    processes = body.get("processes")
    if not isinstance(processes, list):
        return []
    victims = []
    for entry in processes:
        if isinstance(entry, dict) and entry.get("reason"):
            victims.append(
                {"name": str(entry.get("name", "?")), "reason": str(entry["reason"])}
            )
    return victims


def faulting_frames(body: dict, limit: int) -> list[str]:
    """The faulting thread's stack, innermost first, as `image symbol +offset`."""
    index = body.get("faultingThread")
    threads = body.get("threads")
    if not isinstance(index, int) or not isinstance(threads, list):
        return []
    if not 0 <= index < len(threads):
        return []
    thread = threads[index]
    if not isinstance(thread, dict):
        return []
    images = body.get("usedImages") or []
    out = []
    for frame in (thread.get("frames") or [])[:limit]:
        if isinstance(frame, dict):
            out.append(_format_frame(frame, images))
    return out


def _format_frame(frame: dict, images: list) -> str:
    image_index = frame.get("imageIndex")
    name = "?"
    if isinstance(image_index, int) and 0 <= image_index < len(images):
        image = images[image_index]
        if isinstance(image, dict):
            name = str(image.get("name") or image.get("path") or "?")
    symbol = frame.get("symbol")
    if symbol:
        location = frame.get("symbolLocation")
        suffix = f" + {location}" if isinstance(location, int) else ""
        return f"{name}  {symbol}{suffix}"
    return f"{name}  <{frame.get('imageOffset', '?')}>"
