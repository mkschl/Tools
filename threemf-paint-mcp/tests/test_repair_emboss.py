import zipfile

from threemf_paint_mcp.inspect import inspect_archive
from threemf_paint_mcp.repair_emboss import repair_embossed_paint
from threemf_paint_mcp.threemf_model import ROOT_MODEL

# Fixture meshes are tiny (8 verts/plane-face), so the real min_plane_verts
# default (32) would exclude every plane -- tests pass a threshold that fits.
_MIN_PLANE_VERTS = 4


def test_repair_paints_wall_faces_left_by_camera_angle(embossed_wall_gap_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        embossed_wall_gap_3mf, output, min_plane_verts=_MIN_PLANE_VERTS
    )

    assert result["output_path"] == str(output)
    assert result["triangles_changed"] == 8  # the 8 side-wall triangles

    mesh = result["meshes"][0]
    assert mesh["slot"] == 2
    assert mesh["faces_newly_painted"] == 8
    assert mesh["faces_already_correct"] == 2  # the two glyph-top triangles
    assert mesh["faces_recoloured"] == 0

    after = inspect_archive(output)
    # 2 glyph-top + 8 wall leaf regions, all now slot 2; plate-top stays unpainted.
    assert after["paint_usage_by_slot"] == {"2": 10}


def test_repair_is_idempotent(embossed_wall_gap_3mf, tmp_path):
    first_output = tmp_path / "repaired.3mf"
    repair_embossed_paint(
        embossed_wall_gap_3mf, first_output, min_plane_verts=_MIN_PLANE_VERTS
    )

    second_output = tmp_path / "repaired_again.3mf"
    result = repair_embossed_paint(
        first_output, second_output, min_plane_verts=_MIN_PLANE_VERTS
    )

    assert result["already_complete"] is True
    assert result["output_path"] is None
    assert not second_output.exists()


def test_repair_already_complete_mesh_writes_nothing(embossed_repaired_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        embossed_repaired_3mf, output, min_plane_verts=_MIN_PLANE_VERTS
    )

    assert result["already_complete"] is True
    assert result["output_path"] is None
    assert not output.exists()


def test_repair_dry_run_previews_without_writing(embossed_wall_gap_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        embossed_wall_gap_3mf, output, dry_run=True, min_plane_verts=_MIN_PLANE_VERTS
    )

    assert result["dry_run"] is True
    assert result["output_path"] is None
    assert result["triangles_changed"] == 8
    assert not output.exists()


def test_repair_refuses_on_completely_unpainted_mesh(unpainted_box_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        unpainted_box_3mf, output, min_plane_verts=_MIN_PLANE_VERTS
    )

    assert "error" in result
    assert result["output_path"] is None
    assert not output.exists()


def test_repair_explicit_override_works_on_unpainted_mesh(unpainted_box_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        unpainted_box_3mf,
        output,
        slot=2,
        target_z=1.0,
        min_plane_verts=_MIN_PLANE_VERTS,
    )

    assert result["output_path"] == str(output)
    mesh = result["meshes"][0]
    assert mesh["slot"] == 2
    # All 10 triangles touching z=1 (2 top + 8 walls) get painted; none were
    # painted before, so there's nothing to classify as "already correct".
    assert mesh["faces_newly_painted"] == 10


def test_repair_picks_engraved_floor_not_the_top_plane(engraved_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        engraved_3mf, output, min_plane_verts=_MIN_PLANE_VERTS
    )

    mesh = result["meshes"][0]
    assert mesh["detail_plane_z"] == 0.0  # the painted floor, not the z=1 top
    assert mesh["slot"] == 3
    assert mesh["faces_newly_painted"] == 8  # the 8 wall triangles


def test_repair_preserves_other_zip_entries(embossed_wall_gap_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    repair_embossed_paint(
        embossed_wall_gap_3mf, output, min_plane_verts=_MIN_PLANE_VERTS
    )

    with (
        zipfile.ZipFile(embossed_wall_gap_3mf) as original,
        zipfile.ZipFile(output) as new,
    ):
        assert set(original.namelist()) == set(new.namelist())
        assert original.read(ROOT_MODEL) == new.read(ROOT_MODEL)
        assert original.read("Metadata/project_settings.config") == new.read(
            "Metadata/project_settings.config"
        )


def test_repair_validates_output(embossed_wall_gap_3mf, tmp_path):
    output = tmp_path / "repaired.3mf"
    result = repair_embossed_paint(
        embossed_wall_gap_3mf, output, min_plane_verts=_MIN_PLANE_VERTS
    )
    assert result["validation"]["ok"] is True
