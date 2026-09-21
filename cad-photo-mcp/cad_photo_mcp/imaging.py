"""Image analysis and compositing: silhouettes, grid calibration, overlays."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image

from cad_photo_mcp.geometry import BBox, RenderMapping, rotate_about_centre


@dataclass(frozen=True)
class Backdrop:
    """A renderer's flat background, which is what separates model from empty.

    Supplied by the CAD backend so this module stays format-agnostic.
    """

    colour: tuple[int, int, int]
    tolerance: int


def silhouette_mask(img: Image.Image, backdrop: Backdrop) -> np.ndarray:
    """True where the render differs from the renderer's background colour."""
    arr = np.asarray(img.convert("RGB")).astype(int)
    delta = np.abs(arr - np.array(backdrop.colour)).sum(axis=2)
    return delta > backdrop.tolerance


def mask_bbox(mask: np.ndarray) -> BBox:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        raise ValueError("render is empty")
    return BBox(int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def touches_border(mask: np.ndarray) -> bool:
    """Whether the silhouette runs off the frame.

    A clipped render still looks plausible but its measured width is wrong, which
    silently corrupts every scale derived from it. Always check before mapping.
    """
    return bool(
        mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any()
    )


def outline(mask: np.ndarray) -> np.ndarray:
    """Boundary pixels of a mask, for drawing a crisp edge."""
    interior = (
        np.roll(mask, 1, 0)
        & np.roll(mask, -1, 0)
        & np.roll(mask, 1, 1)
        & np.roll(mask, -1, 1)
    )
    return mask & ~interior


def grid_period_px(img: Image.Image, region: BBox | None = None) -> float:
    """Spacing of a regular grid in the image, in pixels.

    Uses autocorrelation of a horizontal intensity profile, so it does not depend
    on the grid's colour — only on it being periodic.
    """
    arr = np.asarray(img.convert("L")).astype(float)
    if region is not None:
        arr = arr[region.y0 : region.y1, region.x0 : region.x1]
    if arr.size == 0:
        raise ValueError("calibration region is empty")

    profile = arr.mean(axis=0)
    profile = profile - profile.mean()
    if not np.any(profile):
        raise ValueError("no intensity variation in the calibration region")

    correlation = np.correlate(profile, profile, mode="full")[len(profile) - 1 :]
    min_lag = 4
    if correlation.size <= min_lag + 1:
        raise ValueError("calibration region is too narrow to find a grid")

    search = correlation[min_lag:]
    peaks = [
        lag
        for lag in range(1, search.size - 1)
        if search[lag] > search[lag - 1] and search[lag] > search[lag + 1]
    ]
    if not peaks:
        raise ValueError("no periodic grid found in the calibration region")
    best = max(peaks, key=lambda lag: search[lag])
    return float(best + min_lag)


def tint_layer(
    mask: np.ndarray, colour: tuple[int, int, int], opacity: float
) -> Image.Image:
    """A translucent fill with a solid outline, for compositing onto a photo."""
    rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
    rgba[mask] = (*colour, int(255 * opacity))
    rgba[outline(mask)] = (*colour, 255)
    return Image.fromarray(rgba, mode="RGBA")


def composite_overlay(
    photo: Image.Image,
    layers: Sequence[tuple[Image.Image, tuple[int, int, int]]],
    mapping: RenderMapping,
    backdrop: Backdrop,
    photo_px_per_mm: float,
    centre: tuple[float, float],
    rotate_deg: float,
    opacity: float,
) -> Image.Image:
    """Scale each render to the photo's scale and paste it over the photo.

    Every layer must be framed by the same camera as the solid render `mapping`
    came from. A cross-section is narrower than the model, so deriving its scale
    from its own silhouette would shrink it — it borrows the solid's instead.
    """
    canvas = photo.convert("RGBA")
    scale = photo_px_per_mm / mapping.px_per_mm
    for render, colour in layers:
        mask = silhouette_mask(render, backdrop)
        if touches_border(mask):
            raise ValueError("render is clipped; the derived scale would be wrong")

        layer = tint_layer(mask, colour, opacity)
        layer = layer.resize(
            (max(1, round(layer.width * scale)), max(1, round(layer.height * scale))),
            Image.Resampling.LANCZOS,
        )
        ox, oy = mapping.origin_x * scale, mapping.origin_y * scale

        if rotate_deg:
            before = (layer.width, layer.height)
            layer = layer.rotate(
                rotate_deg, resample=Image.Resampling.BICUBIC, expand=True
            )
            ox, oy = rotate_about_centre(
                ox, oy, before, (layer.width, layer.height), rotate_deg
            )

        canvas.alpha_composite(layer, (round(centre[0] - ox), round(centre[1] - oy)))
    return canvas
