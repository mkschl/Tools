import struct

import pytest

from cad_photo_mcp.geometry import (
    BBox,
    Bounds,
    cross_check,
    distance_px,
    parse_stl_bounds,
    render_mapping,
    rotate_about_centre,
)

# Deliberately off-centre and non-square: a model centred on the origin passes a
# broken mapping just as happily as a correct one.
TRIANGLES = [
    [(-5.0, -2.0, 0.0), (10.0, -2.0, 0.0), (10.0, 7.0, 3.0)],
    [(-5.0, -2.0, 0.0), (10.0, 7.0, 3.0), (-5.0, 7.0, 3.0)],
]
EXPECTED = Bounds(-5.0, -2.0, 0.0, 10.0, 7.0, 3.0)


def write_ascii_stl(path, triangles):
    lines = ["solid test"]
    for tri in triangles:
        lines += ["facet normal 0 0 1", "outer loop"]
        lines += [f"vertex {x} {y} {z}" for x, y, z in tri]
        lines += ["endloop", "endfacet"]
    lines.append("endsolid test")
    path.write_text("\n".join(lines))


def write_binary_stl(path, triangles):
    out = bytearray(b"\0" * 80) + struct.pack("<I", len(triangles))
    for tri in triangles:
        out += struct.pack("<fff", 0.0, 0.0, 1.0)
        for vertex in tri:
            out += struct.pack("<fff", *vertex)
        out += struct.pack("<H", 0)
    path.write_bytes(bytes(out))


def test_parses_ascii_stl(tmp_path):
    stl = tmp_path / "m.stl"
    write_ascii_stl(stl, TRIANGLES)
    assert parse_stl_bounds(stl) == EXPECTED


def test_parses_binary_stl(tmp_path):
    stl = tmp_path / "m.stl"
    write_binary_stl(stl, TRIANGLES)
    assert parse_stl_bounds(stl) == EXPECTED


def test_rejects_stl_without_vertices(tmp_path):
    stl = tmp_path / "m.stl"
    write_binary_stl(stl, [])
    with pytest.raises(ValueError, match="no vertices"):
        parse_stl_bounds(stl)


def test_bounds_reports_size():
    assert EXPECTED.width == 15.0
    assert EXPECTED.depth == 9.0
    assert EXPECTED.height == 3.0
    assert EXPECTED.centre_xy == (2.5, 2.5)


def test_mapping_scale_comes_from_silhouette_width():
    model = Bounds(0.0, 0.0, 0.0, 40.0, 20.0, 5.0)
    silhouette = BBox(100, 50, 500, 250)  # 400px across 40mm
    assert render_mapping(model, silhouette, from_below=False).px_per_mm == 10.0


def test_mapping_origin_tracks_an_off_centre_model():
    model = Bounds(-5.0, -2.0, 0.0, 10.0, 7.0, 3.0)  # 15mm x 9mm
    silhouette = BBox(100, 50, 250, 140)  # 150px x 90px -> 10 px/mm
    mapping = render_mapping(model, silhouette, from_below=False)
    assert mapping.px_per_mm == 10.0
    # x=0 lies 5mm right of the model's left edge
    assert mapping.origin_x == pytest.approx(150.0)
    # y=0 lies 2mm above the model's bottom edge, and image y grows downward
    assert mapping.origin_y == pytest.approx(120.0)


def test_mapping_flips_y_when_viewed_from_below():
    model = Bounds(-5.0, -2.0, 0.0, 10.0, 7.0, 3.0)
    silhouette = BBox(100, 50, 250, 140)
    above = render_mapping(model, silhouette, from_below=False)
    below = render_mapping(model, silhouette, from_below=True)
    assert below.origin_x == above.origin_x
    # seen from below the model's -Y edge is at the top of the image instead
    assert below.origin_y == pytest.approx(70.0)
    assert below.origin_y != above.origin_y


def test_mapping_rejects_degenerate_model():
    flat = Bounds(3.0, 0.0, 0.0, 3.0, 10.0, 1.0)
    with pytest.raises(ValueError, match="no width"):
        render_mapping(flat, BBox(0, 0, 10, 10), from_below=False)


def test_rotate_about_centre_is_identity_at_zero():
    assert rotate_about_centre(30.0, 40.0, (100, 80), (100, 80), 0.0) == pytest.approx(
        (30.0, 40.0)
    )


def test_rotate_about_centre_quarter_turn():
    # centre of a 100x100 image, a point 20px to its right, rotated 90deg CCW
    x, y = rotate_about_centre(70.0, 50.0, (100, 100), (100, 100), 90.0)
    assert (x, y) == pytest.approx((50.0, 30.0))


def test_distance_px_is_euclidean():
    assert distance_px(10.0, 20.0, 13.0, 24.0) == pytest.approx(5.0)


def test_cross_check_accepts_agreement_within_tolerance():
    # 100px at 10 px/mm reads 10.0mm; caliper says 10.15mm -> -1.48%
    check = cross_check(10.0, 100.0, 10.15, tolerance_pct=2.0)
    assert check.measured_mm == pytest.approx(10.0)
    assert check.deviation_pct == pytest.approx(-1.478, abs=0.01)
    assert check.agrees


def test_cross_check_rejects_disagreement_in_either_direction():
    # asymmetric: photo reads 9% large, then 9% small
    assert not cross_check(10.0, 109.0, 10.0, tolerance_pct=2.0).agrees
    too_small = cross_check(10.0, 91.0, 10.0, tolerance_pct=2.0)
    assert not too_small.agrees
    assert too_small.deviation_pct == pytest.approx(-9.0)


def test_cross_check_rejects_nonpositive_caliper_value():
    with pytest.raises(ValueError, match="positive"):
        cross_check(10.0, 100.0, 0.0, tolerance_pct=2.0)
