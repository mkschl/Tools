"""Loads config.toml into typed settings. Values live in the TOML, not here."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.toml")

type RGB = tuple[int, int, int]


@dataclass(frozen=True)
class OpenScadSettings:
    executable: str
    colorscheme: str
    background: RGB
    timeout_seconds: float


@dataclass(frozen=True)
class RenderSettings:
    size_px: int
    initial_distance_factor: float
    fit_growth: float
    max_fit_attempts: int
    silhouette_tolerance: int


@dataclass(frozen=True)
class CalibrationSettings:
    cross_check_tolerance_pct: float
    store_dir: Path


@dataclass(frozen=True)
class OverlaySettings:
    solid_colour: RGB
    section_colour: RGB


@dataclass(frozen=True)
class Settings:
    openscad: OpenScadSettings
    render: RenderSettings
    calibration: CalibrationSettings
    overlay: OverlaySettings


def _rgb(raw: list[int]) -> RGB:
    r, g, b = raw
    return int(r), int(g), int(b)


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    raw = tomllib.loads(path.read_text())
    scad, render, cal, overlay = (
        raw["openscad"],
        raw["render"],
        raw["calibration"],
        raw["overlay"],
    )
    return Settings(
        openscad=OpenScadSettings(
            executable=scad["executable"],
            colorscheme=scad["colorscheme"],
            background=_rgb(scad["background"]),
            timeout_seconds=float(scad["timeout_seconds"]),
        ),
        render=RenderSettings(
            size_px=int(render["size_px"]),
            initial_distance_factor=float(render["initial_distance_factor"]),
            fit_growth=float(render["fit_growth"]),
            max_fit_attempts=int(render["max_fit_attempts"]),
            silhouette_tolerance=int(render["silhouette_tolerance"]),
        ),
        calibration=CalibrationSettings(
            cross_check_tolerance_pct=float(cal["cross_check_tolerance_pct"]),
            store_dir=Path(cal["store_dir"]).expanduser(),
        ),
        overlay=OverlaySettings(
            solid_colour=_rgb(overlay["solid_colour"]),
            section_colour=_rgb(overlay["section_colour"]),
        ),
    )
