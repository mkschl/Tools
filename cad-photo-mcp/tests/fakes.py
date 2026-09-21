"""Test doubles shared across test modules."""

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from cad_photo_mcp.geometry import Bounds
from cad_photo_mcp.imaging import Backdrop

BACKDROP = Backdrop((255, 255, 229), 12)


@dataclass
class FakeBackend:
    """Stands in for the OpenSCAD subprocess by drawing rectangles.

    Obeys the CadBackend framing contract: a square orthographic view centred on
    the model's XY centre, `distance` millimetres across. The section is a
    fixed, deliberately off-centre footprint smaller than the model, so a
    section that derived its own scale would visibly shrink.
    """

    model: Bounds
    section_footprint: tuple[float, float, float, float]  # x0, y0, x1, y1 in mm
    size_px: int = 200
    distances: list[float] = field(default_factory=list)

    @property
    def backdrop(self) -> Backdrop:
        return BACKDROP

    def available(self) -> bool:
        return True

    def bounds(self, source: Path) -> Bounds:
        return self.model

    def _draw(self, out, rect_mm, from_below, distance):
        k = self.size_px / distance
        cx, cy = self.model.centre_xy
        half = self.size_px / 2
        x0, y0, x1, y1 = rect_mm

        def py(y):
            return half + (y - cy) * k if from_below else half - (y - cy) * k

        top, bottom = sorted((py(y0), py(y1)))
        img = Image.new("RGB", (self.size_px, self.size_px), BACKDROP.colour)
        ImageDraw.Draw(img).rectangle(
            [half + (x0 - cx) * k, top, half + (x1 - cx) * k - 1, bottom - 1],
            fill=(40, 40, 40),
        )
        img.save(out)

    def render(self, source, out, model, from_below, distance):
        self.distances.append(distance)
        m = self.model
        self._draw(out, (m.min_x, m.min_y, m.max_x, m.max_y), from_below, distance)

    def section(self, source, out, model, z, from_below, distance):
        self._draw(out, self.section_footprint, from_below, distance)


# Off-centre, non-square model: 20mm x 10mm, origin inside but not at centre.
MODEL = Bounds(-5.0, -2.0, 0.0, 15.0, 8.0, 6.0)
SLICE = (0.0, 0.0, 10.0, 4.0)  # 10mm x 4mm, narrower than the model


def grid_photo(path: Path, period: int, width: int = 400, height: int = 60) -> Path:
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    for x in range(0, width, period):
        draw.line([(x, 0), (x, height - 1)], fill=255)
    img.save(path)
    return path
