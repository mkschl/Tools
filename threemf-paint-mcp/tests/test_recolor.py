import zipfile

from threemf_paint_mcp.inspect import inspect_archive
from threemf_paint_mcp.recolor import recolor_slots
from threemf_paint_mcp.threemf_model import ROOT_MODEL


def test_recolor_remaps_slot_across_plain_and_subdivided_triangles(
    single_object_3mf, tmp_path
):
    output = tmp_path / "recolored.3mf"
    summary = recolor_slots(single_object_3mf, {"4": 2}, output)

    assert summary["triangles_changed"] == 2  # both triangles carry a slot-4 leaf
    assert summary["distinct_codes_changed"] == 2
    assert summary["validation"]["ok"] is True

    after = inspect_archive(output)
    assert after["paint_usage_by_slot"] == {"1": 1, "2": 2}
    assert 4 not in [int(s) for s in after["paint_usage_by_slot"]]


def test_recolor_reports_slots_now_unused(single_object_3mf, tmp_path):
    output = tmp_path / "recolored.3mf"
    # Move everything off slot 4 and slot 1 onto slot 3; base extruder is slot 1
    # so slot 1 should NOT show up as unused even though nothing paints it anymore.
    summary = recolor_slots(single_object_3mf, {"4": 3, "1": 3}, output)
    # Slot 1 stays "used" because it's still object 1's base extruder even
    # though nothing paints it anymore; slots 2 and 4 are now unreferenced.
    assert summary["slots_now_unused"] == [2, 4]


def test_recolor_preserves_other_zip_entries(single_object_3mf, tmp_path):
    output = tmp_path / "recolored.3mf"
    recolor_slots(single_object_3mf, {"4": 2}, output)

    with zipfile.ZipFile(single_object_3mf) as original, zipfile.ZipFile(output) as new:
        assert set(original.namelist()) == set(new.namelist())
        assert original.read("Metadata/project_settings.config") == new.read(
            "Metadata/project_settings.config"
        )
        assert original.read(ROOT_MODEL) == new.read(ROOT_MODEL)


def test_recolor_noop_mapping_changes_nothing(single_object_3mf, tmp_path):
    output = tmp_path / "recolored.3mf"
    summary = recolor_slots(single_object_3mf, {}, output)
    assert summary["distinct_codes_changed"] == 0
    assert summary["triangles_changed"] == 0
    assert summary["files_changed"] == []
