import zipfile

from threemf_paint_mcp.archive import validate_archive


def test_validate_archive_ok(single_object_3mf):
    result = validate_archive(single_object_3mf)
    assert result == {"ok": True, "bad_crc": [], "xml_errors": {}}


def test_validate_archive_detects_broken_xml(single_object_3mf, tmp_path):
    broken = tmp_path / "broken.3mf"
    with zipfile.ZipFile(single_object_3mf) as src, zipfile.ZipFile(broken, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "3D/3dmodel.model":
                data = b"<not-well-formed"
            dst.writestr(name, data)

    result = validate_archive(broken)
    assert result["ok"] is False
    assert "3D/3dmodel.model" in result["xml_errors"]
