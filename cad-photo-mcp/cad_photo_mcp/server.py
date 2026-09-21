"""MCP tools for measuring photos and comparing CAD models against them.

Tool bodies stay thin — the real work lives in geometry/imaging/cad. Expected
failures come back as error-shaped dicts rather than exceptions, so the calling
model can react instead of losing the turn.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
from mcp.server.fastmcp import FastMCP
from PIL import Image

from cad_photo_mcp.cad import CadBackend, CadError, OpenScadBackend, render_fitted
from cad_photo_mcp.calibration import Calibration, CalibrationStore, photo_sha256
from cad_photo_mcp.config import load_settings
from cad_photo_mcp.geometry import (
    BBox,
    Bounds,
    RenderMapping,
    cross_check,
    distance_px,
    render_mapping,
)
from cad_photo_mcp.imaging import (
    composite_overlay,
    grid_period_px,
    mask_bbox,
    silhouette_mask,
    touches_border,
)

mcp = FastMCP("cad-photo")
settings = load_settings()
backend: CadBackend = OpenScadBackend(settings.openscad, settings.render)
store = CalibrationStore(settings.calibration.store_dir)
log = structlog.get_logger()

# Anything a top-down photo cannot contain. Attached to measurement results so a
# caller never mistakes an estimate for a measurement.
DEPTH_CAVEAT = (
    "Photos carry no depth information. Heights, wall depths and dish/dome rise "
    "must be measured with calipers — never inferred from an image."
)
UNCHECKED_WARNING = (
    "This photo's calibration was never cross-checked against a caliper-measured "
    "feature. Re-run photo_calibrate with check_points and check_mm before "
    "trusting these numbers."
)


def _error(message: str, **context: object) -> dict:
    log.warning("tool_error", message=message, **context)
    return {"ok": False, "error": message}


def _parse_floats(raw: str, count: int, label: str) -> list[float]:
    parts = [p for p in raw.replace(";", ",").split(",") if p.strip()]
    if len(parts) != count:
        raise ValueError(
            f"{label} needs {count} comma-separated numbers, got {len(parts)}"
        )
    return [float(p) for p in parts]


def _segment_px(raw: str, label: str) -> float:
    x1, y1, x2, y2 = _parse_floats(raw, 4, label)
    length = distance_px(x1, y1, x2, y2)
    if length <= 0:
        raise ValueError(f"{label} must be two distinct points")
    return length


def _with_calibration_status(result: dict, calibration: Calibration) -> dict:
    result["px_per_mm"] = calibration.px_per_mm
    result["cross_checked"] = calibration.cross_checked
    if not calibration.cross_checked:
        result["warning"] = UNCHECKED_WARNING
    return result


def _check_z(model: Bounds, z: float) -> None:
    if not model.min_z <= z <= model.max_z:
        raise ValueError(
            f"z={z:g} is outside the model's height range "
            f"{model.min_z:g}..{model.max_z:g} mm"
        )


def _fit_solid(
    source: Path, out: Path, model: Bounds, from_below: bool
) -> tuple[float, RenderMapping]:
    """Render the solid unclipped; return its camera distance and pixel mapping.

    Anything else rendered at that distance (a cross-section) shares the mapping.
    """
    distance = render_fitted(backend, source, out, model, from_below, settings.render)
    mask = silhouette_mask(Image.open(out), backend.backdrop)
    return distance, render_mapping(model, mask_bbox(mask), from_below)


@mcp.tool()
def photo_calibrate(
    photo_path: str,
    grid_mm: float = 10.0,
    region: str = "",
    reference_points: str = "",
    reference_mm: float = 0.0,
    check_points: str = "",
    check_mm: float = 0.0,
) -> dict:
    """Measure a photo's scale and store it for that photo.

    Two ways to get the scale:
    - grid (default): a regular grid in the photo, e.g. a cutting mat's 1cm
      squares. region optionally narrows the search, as "x0,y0,x1,y1" pixels —
      pick bare grid away from the part.
    - reference: reference_points "x1,y1,x2,y2" spanning a known length,
      reference_mm (e.g. two ruler marks 50mm apart).

    Then cross-check it: check_points "x1,y1,x2,y2" across one feature visible
    in the same photo, and check_mm its caliper-measured size. If the two
    disagree beyond tolerance the calibration is wrong and is not stored.

    Calibration is per-photo: two shots from slightly different heights will not
    share a scale. photo_measure and overlay_compare look it up by photo.
    """
    path = Path(photo_path)
    if not path.is_file():
        return _error(f"no such photo: {photo_path}")
    if bool(reference_points) != (reference_mm > 0):
        return _error("reference mode needs both reference_points and reference_mm > 0")
    if bool(check_points) != (check_mm > 0):
        return _error("a cross-check needs both check_points and check_mm > 0")

    try:
        if reference_points:
            method = "reference"
            px_per_mm = _segment_px(reference_points, "reference_points") / reference_mm
            detail: dict = {"reference_mm": reference_mm}
        else:
            if grid_mm <= 0:
                return _error("grid_mm must be positive")
            box = None
            if region:
                x0, y0, x1, y1 = _parse_floats(region, 4, "region")
                box = BBox(int(x0), int(y0), int(x1), int(y1))
            method = "grid"
            period = grid_period_px(Image.open(path), box)
            px_per_mm = period / grid_mm
            detail = {"grid_period_px": period}

        check = None
        if check_points:
            check = cross_check(
                px_per_mm,
                _segment_px(check_points, "check_points"),
                check_mm,
                settings.calibration.cross_check_tolerance_pct,
            )
    except (ValueError, OSError) as exc:
        return _error(str(exc), photo=photo_path)

    if check is not None and not check.agrees:
        return {
            "ok": False,
            "error": (
                f"calibration disagrees with the caliper by {check.deviation_pct:+.1f}% "
                f"(limit ±{settings.calibration.cross_check_tolerance_pct:g}%). "
                "The calibration is wrong, not the caliper — it was not stored. "
                "Try a different grid region, or a reference length."
            ),
            "px_per_mm": px_per_mm,
            "check_measured_mm": check.measured_mm,
            "check_actual_mm": check.actual_mm,
        }

    calibration = Calibration(
        px_per_mm=px_per_mm,
        method=method,
        photo_sha256=photo_sha256(path),
        photo_path=str(path.resolve()),
        check_measured_mm=check.measured_mm if check else None,
        check_actual_mm=check.actual_mm if check else None,
        check_deviation_pct=check.deviation_pct if check else None,
    )
    try:
        store.save(calibration)
    except OSError as exc:
        return _error(f"could not store calibration: {exc}", photo=photo_path)

    result: dict = {"ok": True, "method": method, **detail}
    if check is not None:
        result["check_measured_mm"] = check.measured_mm
        result["check_deviation_pct"] = check.deviation_pct
    return _with_calibration_status(result, calibration)


@mcp.tool()
def photo_measure(photo_path: str, points: str) -> dict:
    """Distance in millimetres between two pixel points in a calibrated photo.

    points is "x1,y1,x2,y2". Uses the scale stored for this exact photo by
    photo_calibrate. Only in-plane distances are meaningful.
    """
    path = Path(photo_path)
    if not path.is_file():
        return _error(f"no such photo: {photo_path}")
    try:
        x1, y1, x2, y2 = _parse_floats(points, 4, "points")
        calibration = store.load(path)
    except (ValueError, OSError) as exc:
        return _error(str(exc))
    if calibration is None:
        return _error("this photo has no calibration yet; run photo_calibrate on it")

    length = distance_px(x1, y1, x2, y2)
    result = {
        "ok": True,
        "distance_px": length,
        "distance_mm": length / calibration.px_per_mm,
        "caveat": DEPTH_CAVEAT,
    }
    return _with_calibration_status(result, calibration)


@mcp.tool()
def model_bounds(source_path: str) -> dict:
    """Bounding box of a CAD source, in millimetres."""
    path = Path(source_path)
    if not path.is_file():
        return _error(f"no such source: {source_path}")
    try:
        bounds = backend.bounds(path)
    except (CadError, ValueError) as exc:
        return _error(str(exc), source=source_path)

    return {
        "ok": True,
        "min": [bounds.min_x, bounds.min_y, bounds.min_z],
        "max": [bounds.max_x, bounds.max_y, bounds.max_z],
        "size": [bounds.width, bounds.depth, bounds.height],
    }


@mcp.tool()
def model_render(source_path: str, out_path: str, from_below: bool = False) -> dict:
    """Orthographic render of the model, framed so it is never clipped.

    Reports the render's own px_per_mm, derived from the model's bounding box
    rather than from camera settings.
    """
    path = Path(source_path)
    if not path.is_file():
        return _error(f"no such source: {source_path}")
    try:
        bounds = backend.bounds(path)
        out = Path(out_path)
        _, mapping = _fit_solid(path, out, bounds, from_below)
    except (CadError, ValueError, OSError) as exc:
        return _error(str(exc), source=source_path)

    return {
        "ok": True,
        "image_path": str(out),
        "px_per_mm": mapping.px_per_mm,
        "origin_px": [mapping.origin_x, mapping.origin_y],
        "from_below": from_below,
    }


@mcp.tool()
def model_section(
    source_path: str, out_path: str, z: float, from_below: bool = False
) -> dict:
    """Horizontal cross-section at height z, rendered as an image.

    Interior walls and ribs appear in no exterior view — this is the only way to
    check them against a photo of the part's underside. The section is framed
    exactly like model_render's solid (also written, as <out>_solid.png), and
    its px_per_mm comes from that solid, since a slice is usually narrower than
    the whole model.
    """
    path = Path(source_path)
    if not path.is_file():
        return _error(f"no such source: {source_path}")
    try:
        bounds = backend.bounds(path)
        _check_z(bounds, z)
        out = Path(out_path)
        solid = out.with_name(f"{out.stem}_solid.png")
        distance, mapping = _fit_solid(path, solid, bounds, from_below)
        backend.section(path, out, bounds, z, from_below, distance)
        mask = silhouette_mask(Image.open(out), backend.backdrop)
        mask_bbox(mask)  # raises if the slice is empty
        if touches_border(mask):
            return _error("section render is clipped", source=source_path)
    except (CadError, ValueError, OSError) as exc:
        return _error(str(exc), source=source_path)

    return {
        "ok": True,
        "image_path": str(out),
        "solid_image_path": str(solid),
        "z": z,
        "px_per_mm": mapping.px_per_mm,
        "origin_px": [mapping.origin_x, mapping.origin_y],
        "from_below": from_below,
    }


@mcp.tool()
def overlay_compare(
    source_path: str,
    photo_path: str,
    out_path: str,
    centre: str,
    from_below: bool = False,
    rotate_deg: float = 0.0,
    section_z: float | None = None,
    opacity: float = 0.25,
) -> dict:
    """Composite the model over a photo at true scale, for direct comparison.

    The photo must have been calibrated with photo_calibrate; its stored scale
    is used. centre is "x,y": where the model's origin sits in the photo, in
    pixels. It is asked for rather than detected, because a wrongly
    auto-detected centre looks just as plausible as a correct one. Pass
    section_z (mm, within the model's height) to add a cross-section layer
    showing interior walls.
    """
    source, photo, out = Path(source_path), Path(photo_path), Path(out_path)
    if not source.is_file():
        return _error(f"no such source: {source_path}")
    if not photo.is_file():
        return _error(f"no such photo: {photo_path}")
    if not 0 <= opacity <= 1:
        return _error("opacity must be between 0 and 1")

    try:
        cx, cy = _parse_floats(centre, 2, "centre")
        calibration = store.load(photo)
        if calibration is None:
            return _error(
                "this photo has no calibration yet; run photo_calibrate on it"
            )
        bounds = backend.bounds(source)
        if section_z is not None:
            _check_z(bounds, section_z)

        solid = out.with_name(f"{out.stem}_solid.png")
        distance, mapping = _fit_solid(source, solid, bounds, from_below)
        layers = [(Image.open(solid), settings.overlay.solid_colour)]

        if section_z is not None:
            section = out.with_name(f"{out.stem}_section.png")
            backend.section(source, section, bounds, section_z, from_below, distance)
            layers.append((Image.open(section), settings.overlay.section_colour))

        result = composite_overlay(
            Image.open(photo),
            layers,
            mapping,
            backend.backdrop,
            calibration.px_per_mm,
            (cx, cy),
            rotate_deg,
            opacity,
        )
        result.convert("RGB").save(out)
    except (CadError, ValueError, OSError) as exc:
        return _error(str(exc), source=source_path, photo=photo_path)

    return _with_calibration_status(
        {
            "ok": True,
            "image_path": str(out),
            "model_size_mm": [bounds.width, bounds.depth],
            "caveat": DEPTH_CAVEAT,
        },
        calibration,
    )


def configure_logging() -> None:
    """Log to stderr only — stdout carries the JSON-RPC stream."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def main() -> None:
    configure_logging()
    if not backend.available():
        log.warning("openscad_missing", hint="install openscad and put it on PATH")
    mcp.run()


if __name__ == "__main__":
    main()
