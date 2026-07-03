from threemf_paint_mcp import server


def test_registered_tool_names():
    tool_names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert tool_names == {
        "inspect_3mf",
        "recolor_slots",
        "list_plates",
        "extract_plate",
        "validate_3mf",
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


def test_list_and_extract_plate_tools(multi_plate_3mf, tmp_path):
    plates = server.list_plates(str(multi_plate_3mf))
    assert len(plates) == 2

    output = tmp_path / "plate2.3mf"
    result = server.extract_plate(str(multi_plate_3mf), "2", str(output))
    assert result["object_ids"] == ["3"]
