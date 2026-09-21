# cad-photo-mcp

MCP server for reverse-engineering physical parts into CAD from photographs, and
for verifying a CAD model against those photographs.

Built after a session where a replacement part was modelled from two phone photos
and went through six wrong revisions — wrong overall shape, wrong diameter, wrong
tab geometry — because every check was "render it and eyeball it". Overlaying the
render on the photo at true scale caught all of it in one pass. This server exists
so that check is the *first* thing available, not something improvised each time.

Conventions: see `conventions/mcp-server.md` and `conventions/python.md` in the
global config. Everything there applies — FastMCP, stderr-only logging, error-shaped
returns, uv/ruff/pytest. This file covers only what is specific to this domain.

## What the server is for

Given a photo of a part next to a known scale reference (a cutting mat's 1cm grid,
a ruler, a caliper), plus a CAD source file, answer:

- How many millimetres per pixel is this photo?
- How big is this feature in the photo, really?
- Does my model actually match the part, at true scale?
- What does the model's *interior* look like, where photos can't see?

## Domain rules that must not be violated

These are the failure modes that cost a full session. They are not style choices.

**Photos give X/Y, never Z.** A top-down photo contains no depth information.
In-plane geometry (diameters, ring positions, tab outlines, hole placement) can be
measured precisely. Wall heights, dome rise, dish depth, rim depth cannot be
recovered at all. Any tool returning a Z-ish value must label it as an estimate and
say that calipers are required. Never let an estimated depth be presented with the
same confidence as a measured diameter.

**Calibrate from the image, then cross-check against one real measurement.** Derive
px/mm from the grid in the photo, then verify it against a single caliper-measured
feature visible in that same photo. Agreement within a couple of percent means the
calibration holds; disagreement means the calibration is wrong, not the caliper.

**Every photo has its own scale.** Two shots of the same part from slightly
different camera heights had 6.95 and 6.35 px/mm. Never reuse a calibration across
images. Store it per-photo.

**Self-calibrate renders from model geometry.** Get the render's px/mm by exporting
the model's bounding box and measuring the rendered silhouette — never from camera
distance constants, which vary by projection mode and viewport.

**A clipped render silently corrupts everything.** If the silhouette touches the
image border, its measured width is wrong, so the derived scale is wrong, so the
overlay is wrong — and it still produces a plausible-looking picture. Always check
the border and fail loudly. This bug shipped once already.

**Viewing from below flips Y.** A photo of a part's underside is mirrored relative
to a top view. Get this wrong and the overlay is subtly, confusingly off. Cover it
with a test, not a comment.

**Interior structure is invisible from outside.** Walls, ribs and concentric rings
do not appear in any exterior render. Cross-sections (`projection(cut = true)` for
OpenSCAD) are the only way to check them, and were what finally revealed a wall
cutting straight through two rings it should have run behind.

## Tool surface

Keep tools composable — each answers one question, and callers chain them.

- `photo_calibrate` — px/mm from a grid or a stated reference length
- `photo_measure` — distance / diameter between points, in mm, for a calibrated photo
- `model_bounds` — bounding box of a CAD source, in mm
- `model_render` — orthographic render from above or below, with its px/mm reported
- `model_section` — horizontal cross-section at a given height, as an image
- `overlay_compare` — the headline tool: model outline composited on a photo at true
  scale, optionally with a cross-section layer

`photo_calibrate` stores each scale keyed by the photo's SHA-256, under
`calibration.store_dir` in `cad_photo_mcp/config.toml`. `photo_measure` and
`overlay_compare` take no `px_per_mm` — they look the scale up for that exact
image, so one photo's scale can never be applied to another. A cross-check that
disagrees beyond `cross_check_tolerance_pct` is refused and not stored; an
unchecked calibration is stored but every downstream result carries a warning.

Cross-sections borrow the pixel mapping of a solid render made at the same camera
distance. A slice is usually narrower than the model's bounding box, so deriving
its scale from its own silhouette would shrink it.

`overlay_compare` needs the part's origin position in the photo. Take it as an
explicit parameter rather than trying to auto-detect it; detection is fragile under
real lighting, and a wrong auto-detected centre is worse than an asked-for one.

## CAD backend

OpenSCAD is the first backend (invoked as a subprocess). Keep anything OpenSCAD-
specific behind a thin adapter so STEP/FreeCAD can be added without touching the
tool layer. The rest of the pipeline — calibration, scaling, compositing — is
format-agnostic and must stay that way: the renderer's background colour reaches
`imaging` only as a `Backdrop` supplied by the backend.

Sections are cut from an STL the backend exports, not by `include`-ing the source:
`include` also draws the file's own top-level solid, which silently replaces the
2D slice. Renders pin `--colorscheme` so a user's GUI theme can't change the
background that silhouette detection relies on.

Tunable values (render size, fit growth, timeout, tolerance, colours) live in
`cad_photo_mcp/config.toml`, not in source.

## Testing

The subprocess and image I/O are boundaries; mock them. The real logic worth
testing is pure and easy to cover:

- STL/bbox parsing, both ASCII and binary
- mm-to-pixel mapping, including the from-below Y flip
- clipping detection
- silhouette and bounding-box extraction from synthetic images

`tests/fakes.py` has a `FakeBackend` that draws rectangles under the same framing
contract as OpenSCAD, so the tool layer runs end to end without the subprocess.
One test drives the real `openscad` binary and is skipped when it is not on PATH.

Assert on asymmetric fixtures. A model centred on the origin passes a broken mapping
just as happily as a correct one, so use an off-centre bounding box where the right
answer and the plausible-looking wrong answer differ.
