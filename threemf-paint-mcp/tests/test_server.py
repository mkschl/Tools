from threemf_paint_mcp import server


def test_registered_tool_names():
    tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert tool_names == {
        "inspect_3mf",
        "recolor_slots",
        "recolor_by_name",
        "list_plates",
        "extract_plate",
        "validate_3mf",
        "repair_embossed_paint",
        "swap_filament_palette",
        "get_paint_coverage_report",
        "paint_region",
        "diff_3mf",
    }


def test_inspect_3mf_tool(single_object_3mf):
    result = server.inspect_3mf(str(single_object_3mf))
    assert result["filament_palette"]["4"] == "#0000FF"


def test_validate_3mf_tool(single_object_3mf):
    result = server.validate_3mf(str(single_object_3mf))
    assert result["ok"] is True


def test_recolor_and_reinspect_round_trip(single_object_3mf, tmp_path):
    output = tmp_path / "out.3mf"
    summary = server.recolor_slots(str(single_object_3mf), {"4": 2}, str(output))
    assert summary["validation"]["ok"] is True

    reinspected = server.inspect_3mf(str(output))
    assert "4" not in reinspected["paint_usage_by_slot"]


def test_recolor_by_name_tool(named_objects_3mf, tmp_path):
    output = tmp_path / "recolored.3mf"
    result = server.recolor_by_name(str(named_objects_3mf), "Text", 1, str(output))
    assert result["triangles_changed"] == 2
    assert result["matched_object_names"] == ["Text"]


def test_repair_embossed_paint_tool(embossed_wall_gap_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = server.repair_embossed_paint(
        str(embossed_wall_gap_3mf), str(output), min_plane_verts=4
    )
    assert result["triangles_changed"] == 8
    assert result["validation"]["ok"] is True


def test_list_and_extract_plate_tools(multi_plate_3mf, tmp_path):
    plates = server.list_plates(str(multi_plate_3mf))
    assert len(plates) == 2

    output = tmp_path / "plate2.3mf"
    result = server.extract_plate(str(multi_plate_3mf), "2", str(output))
    assert result["object_ids"] == ["3"]


def test_swap_filament_palette_tool(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    result = server.swap_filament_palette(str(single_object_3mf), {"2": "#123456"}, str(output))
    assert result["colours_changed"] == {"2": {"old": "#FF0000", "new": "#123456"}}

    reinspected = server.inspect_3mf(str(output))
    assert reinspected["filament_palette"]["2"] == "#123456"


def test_get_paint_coverage_report_tool(single_object_3mf):
    result = server.get_paint_coverage_report(str(single_object_3mf))
    assert result["objects"][0]["object_id"] == "1"
    assert result["objects"][0]["area_by_slot"] == {"4": 0.5}


def test_paint_region_tool(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    result = server.paint_region(str(region_paint_3mf), str(output), 2, x_max=1)
    assert result["faces_newly_painted"] == 1
    assert result["validation"]["ok"] is True


def test_diff_3mf_tool(single_object_3mf, tmp_path):
    output = tmp_path / "recolored.3mf"
    server.recolor_slots(str(single_object_3mf), {"4": 2}, str(output))
    result = server.diff_3mf(str(single_object_3mf), str(output))
    assert result["identical"] is False
    assert result["paint_usage_diff"]["2"] == {"a": 0, "b": 2}
