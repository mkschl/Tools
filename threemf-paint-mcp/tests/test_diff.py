from tests.fixtures import build_diff_variant_fixture
from threemf_paint_mcp.diff import diff_3mf
from threemf_paint_mcp.palette import swap_filament_palette
from threemf_paint_mcp.plates import extract_plate
from threemf_paint_mcp.recolor import recolor_slots


def test_diff_identical_archives(single_object_3mf):
    result = diff_3mf(single_object_3mf, single_object_3mf)

    assert result["identical"] is True
    assert result["palette_diff"] == {}
    assert result["objects_added"] == []
    assert result["objects_removed"] == []
    assert result["objects_changed"] == {}
    assert result["paint_usage_diff"] == {}
    assert result["triangle_counts"] == {"a": 2, "b": 2}


def test_diff_detects_palette_and_paint_usage_changes(single_object_3mf, tmp_path):
    recolored = tmp_path / "recolored.3mf"
    recolor_slots(single_object_3mf, {"4": 2}, recolored)
    swapped = tmp_path / "swapped.3mf"
    swap_filament_palette(recolored, {"1": "#123456"}, swapped)

    result = diff_3mf(single_object_3mf, swapped)

    assert result["identical"] is False
    assert result["palette_diff"] == {"1": {"a": "#000000", "b": "#123456"}}
    assert result["paint_usage_diff"] == {
        "2": {"a": 0, "b": 2},
        "4": {"a": 2, "b": 0},
    }
    assert result["objects_added"] == []
    assert result["objects_removed"] == []
    assert result["objects_changed"] == {}


def test_diff_detects_added_and_removed_objects(multi_plate_3mf, tmp_path):
    output = tmp_path / "plate1.3mf"
    extract_plate(multi_plate_3mf, "1", output)

    result = diff_3mf(multi_plate_3mf, output)

    assert result["objects_removed"] == ["3"]
    assert result["objects_added"] == []
    assert result["identical"] is False


def test_diff_detects_object_name_and_extruder_changes(tmp_path):
    path_a = build_diff_variant_fixture(tmp_path, "a", name="Text", extruder="1")
    path_b = build_diff_variant_fixture(tmp_path, "b", name="TextV2", extruder="2")

    result = diff_3mf(path_a, path_b)

    assert result["objects_changed"] == {
        "1": {
            "name": {"a": "Text", "b": "TextV2"},
            "base_extruder_slot": {"a": 1, "b": 2},
        }
    }
    assert result["identical"] is False


def test_diff_reports_triangle_count_difference(single_object_3mf, region_paint_3mf):
    result = diff_3mf(single_object_3mf, region_paint_3mf)

    assert result["triangle_counts"] == {"a": 2, "b": 6}
    assert result["identical"] is False
