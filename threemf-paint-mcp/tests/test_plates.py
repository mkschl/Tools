import zipfile

import pytest

from threemf_paint_mcp.plates import PlateNotFoundError, extract_plate, list_plates


def test_list_plates(multi_plate_3mf):
    plates = list_plates(multi_plate_3mf)
    assert [p["plater_id"] for p in plates] == ["1", "2"]
    assert plates[0]["object_ids"] == ["1", "2"]
    assert plates[1]["object_ids"] == ["3"]
    assert plates[0]["thumbnails"]["thumbnail_file"] == "Metadata/plate_1.png"


def test_extract_plate_keeps_only_target_objects(multi_plate_3mf, tmp_path):
    output = tmp_path / "plate1.3mf"
    result = extract_plate(multi_plate_3mf, "1", output)

    assert result["object_ids"] == ["1", "2"]
    assert result["validation"]["ok"] is True
    assert result["warnings"]  # caveats from the plan should always surface

    with zipfile.ZipFile(output) as zf:
        names = set(zf.namelist())
        assert "3D/Objects/object_1.model" in names
        assert "3D/Objects/object_2.model" in names
        assert "3D/Objects/object_3.model" not in names
        assert "Metadata/plate_1.png" in names
        assert "Metadata/plate_2.png" not in names


def test_extract_plate_unknown_plater_id_raises(multi_plate_3mf, tmp_path):
    with pytest.raises(PlateNotFoundError):
        extract_plate(multi_plate_3mf, "999", tmp_path / "out.3mf")
