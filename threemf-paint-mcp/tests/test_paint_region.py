import xml.etree.ElementTree as ET
import zipfile

import pytest

from reference.paint_codec_reference import Leaf, encode
from threemf_paint_mcp.inspect import inspect_archive
from threemf_paint_mcp.paint_region import RegionPaintError, paint_region
from threemf_paint_mcp.threemf_model import iter_local


def _triangle_paint_colors(archive_path) -> list[str | None]:
    with zipfile.ZipFile(archive_path) as zf:
        root = ET.fromstring(zf.read("3D/Objects/object_1.model"))
    return [t.attrib.get("paint_color") for t in iter_local(root, "triangle")]


def test_paint_region_classifies_selected_triangles(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    # x_max=1 selects only cluster A (vertices at x=0..1): one unpainted,
    # one slot-1, one already slot-2, and one split triangle -- hits all
    # four classification paths. Cluster B (x=10..11) and cluster C
    # (straddles x=2) are both fully outside x<=1.
    result = paint_region(region_paint_3mf, output, slot=2, x_max=1)

    assert result["faces_newly_painted"] == 1
    assert result["faces_recoloured"] == 1
    assert result["faces_already_correct"] == 1
    assert result["faces_skipped_multi_region"] == 1
    assert result["validation"]["ok"] is True

    after = inspect_archive(output)
    # 3 leaves now carry slot 2 (was-unpainted, was-slot-1, already-slot-2);
    # the split triangle's slot-1/slot-4 children are untouched.
    assert after["paint_usage_by_slot"]["2"] == 3


def test_paint_region_leaves_out_of_bounds_triangles_untouched(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    paint_region(region_paint_3mf, output, slot=2, x_max=1)

    colors = _triangle_paint_colors(output)
    # Triangle order matches fixtures.py's build_region_paint_fixture:
    # 4 cluster-A triangles, then cluster B, then cluster C.
    assert colors[4] is None  # cluster B (x=10..11), untouched
    assert colors[5] is None  # cluster C (straddles x=2), untouched
    assert colors[0] == encode(Leaf(state=2))  # cluster A, newly painted


def test_paint_region_mode_any_includes_straddling_triangle(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    result = paint_region(region_paint_3mf, output, slot=3, x_min=2, x_max=3, mode="any")
    # Cluster C has vertices at x=1.5, 2.5, 1.5 -- only the x=2.5 vertex is
    # inside [2, 3], but mode="any" selects the triangle anyway.
    assert result["faces_newly_painted"] == 1


def test_paint_region_mode_all_excludes_straddling_triangle(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    result = paint_region(region_paint_3mf, output, slot=3, x_min=2, x_max=3, mode="all")
    assert result["faces_newly_painted"] == 0
    assert result["already_complete"] is True
    assert result["output_path"] is None
    assert not output.exists()


def test_paint_region_requires_at_least_one_bound(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    with pytest.raises(RegionPaintError, match="at least one"):
        paint_region(region_paint_3mf, output, slot=2)


def test_paint_region_rejects_invalid_mode(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    with pytest.raises(RegionPaintError, match="mode must be"):
        paint_region(region_paint_3mf, output, slot=2, x_max=2, mode="sideways")


def test_paint_region_dry_run_writes_nothing(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    result = paint_region(region_paint_3mf, output, slot=2, x_max=1, dry_run=True)

    assert result["dry_run"] is True
    assert result["output_path"] is None
    assert not output.exists()


def test_paint_region_object_ids_missing_raises(region_paint_3mf, tmp_path):
    output = tmp_path / "painted.3mf"
    with pytest.raises(RegionPaintError, match="object_ids not found"):
        paint_region(region_paint_3mf, output, slot=2, x_max=1, object_ids=["999"])
