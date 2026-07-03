from threemf_paint_mcp.inspect import inspect_archive


def test_inspect_single_object_fixture(single_object_3mf):
    result = inspect_archive(single_object_3mf)

    assert result["filament_palette"] == {
        "1": "#000000",
        "2": "#FF0000",
        "3": "#00FF00",
        "4": "#0000FF",
    }
    assert result["objects"] == [{"object_id": "1", "base_extruder_slot": 1}]
    # PAINT_LEAF_SLOT_4 -> one leaf of state 4.
    # PAINT_SPLIT_MIXED -> leaves of state 4, 1, 0 (0 is the "unpainted" leaf).
    assert result["paint_usage_by_slot"] == {"1": 1, "4": 2}
    assert result["unpainted_leaf_regions"] == 1
