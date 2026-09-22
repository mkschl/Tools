"""Loads config.toml into typed settings. Values live in the TOML, not here."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.toml")


@dataclass(frozen=True)
class ShellSettings:
    allowed_commands: frozenset[str]
    default_timeout_seconds: float
    spindump_timeout_seconds: float
    max_line_bytes: int


@dataclass(frozen=True)
class LogSettings:
    default_window: str
    max_window_seconds: int
    max_scanned_lines: int
    default_group_limit: int
    max_group_limit: int
    default_sample_lines: int
    max_sample_lines: int
    auth_origin_lines: int
    presets: dict[str, str]


@dataclass(frozen=True)
class ModalSettings:
    auth_processes: tuple[str, ...]
    touchid_window: str
    touchid_predicate: str
    touchid_assert_markers: tuple[str, ...]
    touchid_clear_markers: tuple[str, ...]
    touchid_renewal_seconds: int
    touchid_sample_lines: int


@dataclass(frozen=True)
class ReportSettings:
    directories: tuple[Path, ...]
    default_limit: int
    max_limit: int
    max_frames: int
    spindump_suffixes: tuple[str, ...]
    spindump_head_lines: int


@dataclass(frozen=True)
class TimelineSettings:
    default_wake_count: int
    max_wake_count: int


@dataclass(frozen=True)
class RedactSettings:
    enabled: bool
    home_placeholder: str
    user_placeholder: str
    host_placeholder: str
    ssid_placeholder: str
    ip_placeholder: str
    mac_placeholder: str
    email_placeholder: str
    min_username_length: int


@dataclass(frozen=True)
class Settings:
    shell: ShellSettings
    log: LogSettings
    modal: ModalSettings
    reports: ReportSettings
    timeline: TimelineSettings
    redact: RedactSettings


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    raw = tomllib.loads(path.read_text())
    shell, log, modal, reports, timeline, redact = (
        raw["shell"],
        raw["log"],
        raw["modal"],
        raw["reports"],
        raw["timeline"],
        raw["redact"],
    )
    return Settings(
        shell=ShellSettings(
            allowed_commands=frozenset(shell["allowed_commands"]),
            default_timeout_seconds=float(shell["default_timeout_seconds"]),
            spindump_timeout_seconds=float(shell["spindump_timeout_seconds"]),
            max_line_bytes=int(shell["max_line_bytes"]),
        ),
        log=LogSettings(
            default_window=log["default_window"],
            max_window_seconds=int(log["max_window_seconds"]),
            max_scanned_lines=int(log["max_scanned_lines"]),
            default_group_limit=int(log["default_group_limit"]),
            max_group_limit=int(log["max_group_limit"]),
            default_sample_lines=int(log["default_sample_lines"]),
            max_sample_lines=int(log["max_sample_lines"]),
            auth_origin_lines=int(log["auth_origin_lines"]),
            presets=dict(log["presets"]),
        ),
        modal=ModalSettings(
            auth_processes=tuple(modal["auth_processes"]),
            touchid_window=modal["touchid_window"],
            touchid_predicate=modal["touchid_predicate"],
            touchid_assert_markers=tuple(modal["touchid_assert_markers"]),
            touchid_clear_markers=tuple(modal["touchid_clear_markers"]),
            touchid_renewal_seconds=int(modal["touchid_renewal_seconds"]),
            touchid_sample_lines=int(modal["touchid_sample_lines"]),
        ),
        reports=ReportSettings(
            directories=tuple(Path(d).expanduser() for d in reports["directories"]),
            default_limit=int(reports["default_limit"]),
            max_limit=int(reports["max_limit"]),
            max_frames=int(reports["max_frames"]),
            spindump_suffixes=tuple(reports["spindump_suffixes"]),
            spindump_head_lines=int(reports["spindump_head_lines"]),
        ),
        timeline=TimelineSettings(
            default_wake_count=int(timeline["default_wake_count"]),
            max_wake_count=int(timeline["max_wake_count"]),
        ),
        redact=RedactSettings(
            enabled=bool(redact["enabled"]),
            home_placeholder=redact["home_placeholder"],
            user_placeholder=redact["user_placeholder"],
            host_placeholder=redact["host_placeholder"],
            ssid_placeholder=redact["ssid_placeholder"],
            ip_placeholder=redact["ip_placeholder"],
            mac_placeholder=redact["mac_placeholder"],
            email_placeholder=redact["email_placeholder"],
            min_username_length=int(redact["min_username_length"]),
        ),
    )
