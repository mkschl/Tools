"""CAD backend adapter.

Everything format-specific lives behind `CadBackend` so another kernel can be
added without touching the tool layer. OpenSCAD is the only backend today.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import structlog
from PIL import Image

from cad_photo_mcp.config import OpenScadSettings, RenderSettings
from cad_photo_mcp.geometry import Bounds, parse_stl_bounds
from cad_photo_mcp.imaging import Backdrop, silhouette_mask, touches_border

log = structlog.get_logger()


class CadError(RuntimeError):
    """The CAD kernel refused to render or export."""


class CadBackend(Protocol):
    """Renders are orthographic, centred on the model's XY centre, and square.

    Two renders of the same model at the same distance therefore share one
    pixel mapping, which is what lets a cross-section borrow a solid's scale.
    """

    @property
    def backdrop(self) -> Backdrop: ...

    def available(self) -> bool: ...

    def bounds(self, source: Path) -> Bounds: ...

    def render(
        self, source: Path, out: Path, model: Bounds, from_below: bool, distance: float
    ) -> None: ...

    def section(
        self,
        source: Path,
        out: Path,
        model: Bounds,
        z: float,
        from_below: bool,
        distance: float,
    ) -> None: ...


@dataclass(frozen=True)
class OpenScadBackend:
    """Drives the `openscad` CLI as a subprocess."""

    settings: OpenScadSettings
    render_settings: RenderSettings

    @property
    def backdrop(self) -> Backdrop:
        return Backdrop(
            self.settings.background, self.render_settings.silhouette_tolerance
        )

    def available(self) -> bool:
        return shutil.which(self.settings.executable) is not None

    def _run(self, source: Path, out: Path, extra: list[str]) -> None:
        cmd = [self.settings.executable, "-o", str(out), *extra, str(source)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CadError(
                f"{self.settings.executable} not found; install OpenSCAD and put it on PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CadError(
                f"openscad did not finish within {self.settings.timeout_seconds:g}s"
            ) from exc
        if result.returncode != 0:
            log.error("openscad_failed", stderr=result.stderr.strip()[:2000])
            raise CadError(result.stderr.strip() or "openscad exited non-zero")

    def bounds(self, source: Path) -> Bounds:
        with _temp_path(".stl") as stl:
            self._run(source, stl, [])
            return parse_stl_bounds(stl)

    def _camera(self, model: Bounds, from_below: bool, distance: float) -> list[str]:
        cx, cy = model.centre_xy
        rot_x = 180 if from_below else 0
        size = self.render_settings.size_px
        return [
            f"--camera={cx},{cy},0,{rot_x},0,0,{distance}",
            "--projection=o",
            f"--imgsize={size},{size}",
            f"--colorscheme={self.settings.colorscheme}",
        ]

    def render(
        self, source: Path, out: Path, model: Bounds, from_below: bool, distance: float
    ) -> None:
        self._run(source, out, self._camera(model, from_below, distance))

    def section(
        self,
        source: Path,
        out: Path,
        model: Bounds,
        z: float,
        from_below: bool,
        distance: float,
    ) -> None:
        # Slice an exported mesh rather than re-including the source: `include`
        # would also draw the file's own top-level solid, which silently
        # replaces the 2D slice, and `use` needs a known module name.
        with _temp_path(".stl") as stl:
            self._run(source, stl, [])
            wrapper = stl.with_suffix(".scad")
            wrapper.write_text(section_wrapper(stl, z))
            self._run(wrapper, out, self._camera(model, from_below, distance))


def section_wrapper(mesh: Path, z: float) -> str:
    """OpenSCAD source that slices `mesh` horizontally at height z."""
    literal = str(mesh.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    return f'projection(cut = true) translate([0, 0, {-z}]) import("{literal}");\n'


def render_fitted(
    backend: CadBackend,
    source: Path,
    out: Path,
    model: Bounds,
    from_below: bool,
    settings: RenderSettings,
) -> float:
    """Render at a distance that frames the whole model, returning that distance.

    A clipped render corrupts the scale silently, so widen until it fits rather
    than trusting a fixed camera distance.
    """
    distance = settings.initial_distance_factor * max(model.width, model.depth)
    for _ in range(settings.max_fit_attempts):
        backend.render(source, out, model, from_below, distance)
        if not touches_border(silhouette_mask(Image.open(out), backend.backdrop)):
            return distance
        distance *= settings.fit_growth
    raise CadError("could not frame the model without clipping")


@contextmanager
def _temp_path(suffix: str) -> Iterator[Path]:
    """A scratch path for subprocess output, cleaned up afterwards."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / f"scratch{suffix}"
