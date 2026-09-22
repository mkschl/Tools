"""Fixtures shaped like the real reports: an .ips is two JSON documents, not one."""

from __future__ import annotations

import json
from pathlib import Path

CRASH_BODY = {
    "procName": "Music",
    "exception": {"type": "EXC_CRASH", "signal": "SIGABRT"},
    "termination": {"namespace": "SIGNAL", "indicator": "Abort trap: 6"},
    "faultingThread": 1,
    "usedImages": [
        {"name": "libsystem_kernel.dylib"},
        {"name": "IconServices"},
    ],
    "threads": [
        {"frames": [{"imageIndex": 0, "symbol": "not_the_faulting_thread"}]},
        {
            "frames": [
                {"imageIndex": 0, "symbol": "__pthread_kill", "symbolLocation": 8},
                {
                    "imageIndex": 1,
                    "symbol": "-[ISIconManager _init]",
                    "symbolLocation": 120,
                },
                {"imageIndex": 9, "imageOffset": 4242},
            ]
        },
    ],
}

SIMULATED_BODY = {
    "procName": "IconServices",
    "is_simulated": 1,
    "faultingThread": 0,
    "usedImages": [{"name": "IconServices"}],
    "threads": [{"frames": [{"imageIndex": 0, "symbol": "-[ISIconManager _init]"}]}],
}

JETSAM_BODY = {
    "processes": [
        {"name": "Safari", "reason": "per-process-limit"},
        {"name": "WindowServer"},
    ]
}


def write_ips(
    directory: Path, name: str, body: dict, header: dict | None = None
) -> Path:
    path = directory / name
    head = header or {
        "app_name": body.get("procName", "?"),
        "bug_type": "309",
        "timestamp": "2026-09-22 07:45:01.00 +0200",
        "os_version": "macOS 27.0 (26A428)",
    }
    path.write_text(f"{json.dumps(head)}\n{json.dumps(body)}")
    return path
