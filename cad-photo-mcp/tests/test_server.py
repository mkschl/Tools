import numpy as np
import pytest
from fakes import SLICE, FakeBackend, grid_photo
from PIL import Image

from cad_photo_mcp import server
from cad_photo_mcp.cad import OpenScadBackend
from cad_photo_mcp.geometry import Bounds

# FakeBackend renders 200px across `distance` mm, and the first fitted distance
# is 3 x the model's 20mm width, so a correctly derived render scale is 200/60.
RENDER_PX_PER_MM = 200 / 60


@pytest.fixture
def photo(tmp_path):
    return grid_photo(tmp_path / "photo.png", period=16)


@pytest.fixture
def blank_photo(tmp_path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (400, 400), (0, 0, 0)).save(path)
    return path


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "part.scad"
    path.write_text("cube(1);\n")
    return path


def calibrate_at_5px_per_mm(photo):
    """100px spans a 20mm reference; a 50px feature checks out at 10mm."""
    return server.photo_calibrate(
        str(photo),
        reference_points="0,0,100,0",
        reference_mm=20.0,
        check_points="0,10,50,10",
        check_mm=10.0,
    )


def test_registered_tool_names():
    # cheap regression check against accidental renames or removals
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert names == {
        "photo_calibrate",
        "photo_measure",
        "model_bounds",
        "model_render",
        "model_section",
        "overlay_compare",
    }


# --- photo_calibrate ---------------------------------------------------------


def test_photo_calibrate_reads_grid_and_flags_it_unchecked(photo):
    result = server.photo_calibrate(str(photo), grid_mm=10.0)
    assert result["ok"]
    assert result["method"] == "grid"
    assert result["px_per_mm"] == pytest.approx(1.6, abs=0.1)
    assert result["cross_checked"] is False
    assert "caliper" in result["warning"]


def test_photo_calibrate_from_a_reference_length(blank_photo):
    result = server.photo_calibrate(
        str(blank_photo), reference_points="10,10,10,160", reference_mm=25.0
    )
    assert result["ok"]
    assert result["method"] == "reference"
    assert result["px_per_mm"] == pytest.approx(6.0)


def test_photo_calibrate_accepts_an_agreeing_cross_check(blank_photo):
    result = calibrate_at_5px_per_mm(blank_photo)
    assert result["ok"]
    assert result["cross_checked"] is True
    assert result["check_deviation_pct"] == pytest.approx(0.0)
    assert "warning" not in result


def test_photo_calibrate_refuses_to_store_a_disagreeing_scale(blank_photo):
    # the feature reads 10mm but calipers say 9mm: +11%, so the scale is wrong
    result = server.photo_calibrate(
        str(blank_photo),
        reference_points="0,0,100,0",
        reference_mm=20.0,
        check_points="0,10,50,10",
        check_mm=9.0,
    )
    assert result["ok"] is False
    assert "+11.1%" in result["error"]
    assert "not stored" in result["error"]
    assert server.photo_measure(str(blank_photo), "0,0,10,0")["ok"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reference_points": "0,0,10,0"},
        {"reference_mm": 20.0},
        {"check_points": "0,0,10,0"},
        {"check_mm": 5.0},
        {"reference_points": "5,5,5,5", "reference_mm": 20.0},
    ],
)
def test_photo_calibrate_rejects_half_specified_inputs(blank_photo, kwargs):
    assert server.photo_calibrate(str(blank_photo), **kwargs)["ok"] is False


def test_photo_calibrate_reports_missing_file_without_raising():
    result = server.photo_calibrate("/nowhere/nope.png")
    assert result == {"ok": False, "error": "no such photo: /nowhere/nope.png"}


def test_photo_calibrate_rejects_nonpositive_grid(photo):
    assert server.photo_calibrate(str(photo), grid_mm=0)["ok"] is False


# --- photo_measure -------------------------------------------------------------


def test_photo_measure_requires_a_calibration(blank_photo):
    result = server.photo_measure(str(blank_photo), points="0,0,6,8")
    assert result["ok"] is False
    assert "photo_calibrate" in result["error"]


def test_photo_measure_uses_the_stored_scale(blank_photo):
    calibrate_at_5px_per_mm(blank_photo)
    result = server.photo_measure(str(blank_photo), points="0,0,30,40")
    assert result["ok"]
    assert result["distance_px"] == pytest.approx(50.0)
    assert result["distance_mm"] == pytest.approx(10.0)
    assert result["cross_checked"] is True
    assert "calipers" in result["caveat"]


def test_photo_measure_never_borrows_another_photos_scale(tmp_path):
    near = grid_photo(tmp_path / "near.png", period=16)
    far = grid_photo(tmp_path / "far.png", period=12)
    server.photo_calibrate(str(near), grid_mm=10.0)
    server.photo_calibrate(str(far), grid_mm=10.0)
    near_mm = server.photo_measure(str(near), "0,0,120,0")["distance_mm"]
    far_mm = server.photo_measure(str(far), "0,0,120,0")["distance_mm"]
    assert near_mm == pytest.approx(75.0, rel=0.07)
    assert far_mm == pytest.approx(100.0, rel=0.1)


def test_photo_measure_warns_when_calibration_is_unchecked(photo):
    server.photo_calibrate(str(photo), grid_mm=10.0)
    result = server.photo_measure(str(photo), points="0,0,3,4")
    assert result["cross_checked"] is False
    assert "caliper" in result["warning"]


def test_photo_measure_rejects_malformed_points(photo):
    result = server.photo_measure(str(photo), points="0,0,5")
    assert result["ok"] is False
    assert "4 comma-separated" in result["error"]


# --- model tools ---------------------------------------------------------------


def test_model_tools_report_missing_source(tmp_path):
    out = str(tmp_path / "o.png")
    assert server.model_bounds("/nowhere/x.scad")["ok"] is False
    assert server.model_render("/nowhere/x.scad", out)["ok"] is False
    assert server.model_section("/nowhere/x.scad", out, 1.0)["ok"] is False


def test_model_bounds_reports_missing_openscad_without_raising(source, monkeypatch):
    settings = server.settings
    missing = type(settings.openscad)(
        **{**settings.openscad.__dict__, "executable": "definitely-not-openscad-xyz"}
    )
    monkeypatch.setattr(server, "backend", OpenScadBackend(missing, settings.render))
    result = server.model_bounds(str(source))
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_model_render_reports_scale_and_origin(fake_backend, source, tmp_path):
    result = server.model_render(str(source), str(tmp_path / "r.png"))
    assert result["ok"]
    assert result["px_per_mm"] == pytest.approx(RENDER_PX_PER_MM, rel=0.02)
    # image centre (100,100) is the model's XY centre (5,3); y grows downward
    ox, oy = result["origin_px"]
    assert ox == pytest.approx(100 - 5 * RENDER_PX_PER_MM, abs=1)
    assert oy == pytest.approx(100 + 3 * RENDER_PX_PER_MM, abs=1)


def test_model_section_takes_its_scale_from_the_solid(fake_backend, source, tmp_path):
    # Regression: a 10mm-wide slice of a 20mm model must not report half the scale.
    out = tmp_path / "s.png"
    result = server.model_section(str(source), str(out), 3.0)
    assert result["ok"]
    assert result["px_per_mm"] == pytest.approx(RENDER_PX_PER_MM, rel=0.02)
    assert out.is_file()
    assert (tmp_path / "s_solid.png").is_file()


def test_model_section_rejects_heights_outside_the_model(
    fake_backend, source, tmp_path
):
    result = server.model_section(str(source), str(tmp_path / "s.png"), 6.5)
    assert result["ok"] is False
    assert "height range" in result["error"]


# --- overlay_compare -------------------------------------------------------------


def colour_extent(image_path, is_colour):
    arr = np.asarray(Image.open(image_path).convert("RGB")).astype(int)
    hit = is_colour(arr)
    rows, cols = np.flatnonzero(hit.any(axis=1)), np.flatnonzero(hit.any(axis=0))
    return cols[0], rows[0], cols[-1] + 1, rows[-1] + 1


def is_section(arr):
    return (arr[..., 1] > 110) & (arr[..., 0] < 110)


def is_solid(arr):
    return (arr[..., 0] > 128) & (arr[..., 1] < 110)


def test_overlay_compare_requires_a_calibrated_photo(
    fake_backend, source, blank_photo, tmp_path
):
    result = server.overlay_compare(
        str(source), str(blank_photo), str(tmp_path / "o.png"), "200,200"
    )
    assert result["ok"] is False
    assert "photo_calibrate" in result["error"]


@pytest.mark.parametrize("from_below", [False, True])
def test_overlay_compare_draws_solid_and_section_at_true_scale(
    fake_backend, source, blank_photo, tmp_path, from_below
):
    calibrate_at_5px_per_mm(blank_photo)
    out = tmp_path / "o.png"
    result = server.overlay_compare(
        str(source),
        str(blank_photo),
        str(out),
        "200,200",
        from_below=from_below,
        section_z=3.0,
        opacity=1.0,
    )
    assert result["ok"], result
    assert result["px_per_mm"] == pytest.approx(5.0)

    # model spans x -5..15, y -2..8 mm; at 5 px/mm about the origin (200,200)
    x0, y0, x1, y1 = colour_extent(out, is_solid)
    assert (x0, x1) == pytest.approx((175, 275), abs=2)
    assert (y0, y1) == pytest.approx((190, 240) if from_below else (160, 210), abs=2)

    # slice spans x 0..10, y 0..4 mm: 50x20 px, not stretched to the model's size
    x0, y0, x1, y1 = colour_extent(out, is_section)
    assert (x0, x1) == pytest.approx((200, 250), abs=2)
    assert (y0, y1) == pytest.approx((200, 220) if from_below else (180, 200), abs=2)


def test_overlay_compare_allows_sections_below_zero(
    monkeypatch, source, blank_photo, tmp_path
):
    # section_z used to default to -1 as "off", which made z < 0 unreachable
    sunk = Bounds(-5.0, -2.0, -4.0, 15.0, 8.0, 2.0)
    monkeypatch.setattr(server, "backend", FakeBackend(sunk, SLICE))
    calibrate_at_5px_per_mm(blank_photo)
    result = server.overlay_compare(
        str(source),
        str(blank_photo),
        str(tmp_path / "o.png"),
        "200,200",
        section_z=-1.0,
    )
    assert result["ok"], result
    assert (tmp_path / "o_section.png").is_file()


@pytest.mark.parametrize(
    "kwargs", [{"section_z": 9.0}, {"opacity": 1.5}, {"centre": "1,2,3"}]
)
def test_overlay_compare_rejects_bad_inputs(
    fake_backend, source, blank_photo, tmp_path, kwargs
):
    calibrate_at_5px_per_mm(blank_photo)
    args = {"centre": "200,200", **kwargs}
    result = server.overlay_compare(
        str(source), str(blank_photo), str(tmp_path / "o.png"), **args
    )
    assert result["ok"] is False


def test_overlay_compare_reports_missing_photo(source, tmp_path):
    result = server.overlay_compare(
        str(source), "/nowhere/p.jpg", str(tmp_path / "o.png"), "10,10"
    )
    assert result["ok"] is False
