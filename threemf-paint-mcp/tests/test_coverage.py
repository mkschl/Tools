import pytest

from threemf_paint_mcp.coverage import CoverageError, get_coverage_report


def test_coverage_computes_area_by_slot_and_split(single_object_3mf):
    # Right-triangle mesh, legs length 1 -> each of the 2 triangles has
    # area 0.5. One is a plain slot-4 leaf, the other a split (mixed) node.
    report = get_coverage_report(single_object_3mf)

    assert len(report["objects"]) == 1
    obj = report["objects"][0]
    assert obj["object_id"] == "1"
    assert obj["base_extruder_slot"] == 1
    assert obj["total_surface_area"] == pytest.approx(1.0)
    assert obj["total_triangles"] == 2
    assert obj["area_by_slot"] == {"4": pytest.approx(0.5)}
    assert obj["triangles_by_slot"] == {"4": 1}
    assert obj["split_area"] == pytest.approx(0.5)
    assert obj["split_triangles"] == 1
    assert obj["unpainted_area"] == pytest.approx(0.0)
    assert obj["painted_fraction"] == pytest.approx(0.5)


def test_coverage_reports_every_object_with_name(named_objects_3mf):
    report = get_coverage_report(named_objects_3mf)
    by_id = {obj["object_id"]: obj for obj in report["objects"]}

    assert by_id["1"]["name"] == "Text"
    assert by_id["1"]["total_surface_area"] == pytest.approx(1.0)
    assert by_id["1"]["area_by_slot"] == {"4": pytest.approx(1.0)}

    assert by_id["2"]["name"] == "Base"
    assert by_id["2"]["total_surface_area"] == pytest.approx(0.5)
    assert by_id["2"]["area_by_slot"] == {"4": pytest.approx(0.5)}


def test_coverage_object_ids_scopes_to_requested_objects(named_objects_3mf):
    report = get_coverage_report(named_objects_3mf, object_ids=["1"])
    assert [obj["object_id"] for obj in report["objects"]] == ["1"]


def test_coverage_object_ids_missing_raises(named_objects_3mf):
    with pytest.raises(CoverageError, match="object_ids not found"):
        get_coverage_report(named_objects_3mf, object_ids=["999"])


def test_coverage_fully_unpainted_mesh(unpainted_box_3mf):
    report = get_coverage_report(unpainted_box_3mf)
    obj = report["objects"][0]

    assert obj["total_surface_area"] == pytest.approx(6.0)
    assert obj["unpainted_area"] == pytest.approx(6.0)
    assert obj["unpainted_triangles"] == 12
    assert obj["area_by_slot"] == {}
    assert obj["painted_fraction"] == pytest.approx(0.0)


def test_coverage_flags_small_painted_area_hidden_by_triangle_count(
    embossed_wall_gap_3mf,
):
    # Top painted (slot 2, area 1.0) but the much larger wall area (4.0)
    # and plate top (1.0) are unpainted -- the emboss side-wall trap.
    # Surface-area coverage should surface this even though painted
    # triangles are 2 of 12 (a plausible-looking count on its own).
    report = get_coverage_report(embossed_wall_gap_3mf)
    obj = report["objects"][0]

    assert obj["total_surface_area"] == pytest.approx(6.0)
    assert obj["area_by_slot"] == {"2": pytest.approx(1.0)}
    assert obj["unpainted_area"] == pytest.approx(5.0)
    assert obj["painted_fraction"] == pytest.approx(1.0 / 6.0, abs=1e-6)
