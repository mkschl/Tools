"""Pure geometry: model bounds, pixel boxes, and millimetre-to-pixel mapping.

No image or subprocess I/O lives here, so all of it is directly unit-testable.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Bounds:
    """Model extents in millimetres."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def depth(self) -> float:
        return self.max_y - self.min_y

    @property
    def height(self) -> float:
        return self.max_z - self.min_z

    @property
    def centre_xy(self) -> tuple[float, float]:
        return (self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2


@dataclass(frozen=True)
class BBox:
    """Pixel bounding box, half-open on the far edge."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def parse_stl_bounds(path: Path) -> Bounds:
    data = path.read_bytes()
    if data[:5] == b"solid" and b"facet" in data[:2048]:
        vertices = _ascii_stl_vertices(data.decode("utf-8", errors="replace"))
    else:
        vertices = _binary_stl_vertices(data)
    if not vertices:
        raise ValueError(f"no vertices found in {path.name}")
    xs, ys, zs = zip(*vertices, strict=True)
    return Bounds(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _ascii_stl_vertices(text: str) -> list[tuple[float, float, float]]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            out.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return out


def _binary_stl_vertices(data: bytes) -> list[tuple[float, float, float]]:
    if len(data) < 84:
        return []
    (count,) = struct.unpack_from("<I", data, 80)
    out = []
    for i in range(count):
        base = 84 + i * 50 + 12  # skip the facet normal
        for v in range(3):
            out.append(struct.unpack_from("<fff", data, base + v * 12))
    return out


@dataclass(frozen=True)
class RenderMapping:
    """Where model millimetres land in a render: scale plus the origin's pixel."""

    px_per_mm: float
    origin_x: float
    origin_y: float


def render_mapping(model: Bounds, silhouette: BBox, from_below: bool) -> RenderMapping:
    """Map model millimetres to pixels in an orthographic render.

    The scale is derived from the rendered silhouette rather than from camera
    settings, so it stays correct regardless of projection mode or viewport —
    but only if the render was not clipped, which the caller must check first.
    The silhouette must be of the *whole* model: a cross-section is narrower
    than the model's bounding box, so it has to borrow the mapping of a solid
    render framed by the same camera instead of deriving its own.

    Seen from below, the model's +Y axis points *down* the image instead of up,
    so the Y origin is measured from the opposite edge of the silhouette.
    """
    if model.width <= 0:
        raise ValueError("model has no width along X")
    px_per_mm = silhouette.width / model.width
    origin_x = silhouette.x0 - model.min_x * px_per_mm
    if from_below:
        origin_y = silhouette.y0 - model.min_y * px_per_mm
    else:
        origin_y = silhouette.y1 + model.min_y * px_per_mm
    return RenderMapping(px_per_mm, origin_x, origin_y)


def rotate_about_centre(
    x: float,
    y: float,
    before: tuple[int, int],
    after: tuple[int, int],
    degrees: float,
) -> tuple[float, float]:
    """Track a point through an expanding rotation about the image centre."""
    theta = math.radians(degrees)
    cx, cy = before[0] / 2, before[1] / 2
    dx, dy = x - cx, y - cy
    rx = dx * math.cos(theta) + dy * math.sin(theta)
    ry = -dx * math.sin(theta) + dy * math.cos(theta)
    return rx + after[0] / 2, ry + after[1] / 2


def distance_px(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


@dataclass(frozen=True)
class CrossCheck:
    """A calibration tested against one feature measured with calipers."""

    measured_mm: float
    actual_mm: float
    deviation_pct: float
    agrees: bool


def cross_check(
    px_per_mm: float, feature_px: float, actual_mm: float, tolerance_pct: float
) -> CrossCheck:
    """Compare a feature's photo-derived size with its caliper measurement.

    Disagreement means the calibration is wrong, not the caliper.
    """
    if actual_mm <= 0:
        raise ValueError("caliper measurement must be positive")
    measured = feature_px / px_per_mm
    deviation = (measured - actual_mm) / actual_mm * 100
    return CrossCheck(measured, actual_mm, deviation, abs(deviation) <= tolerance_pct)
