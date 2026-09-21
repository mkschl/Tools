import shutil
import subprocess
from pathlib import Path

import pytest
from fakes import MODEL, SLICE, FakeBackend
from PIL import Image

from cad_photo_mcp.cad import (
    CadError,
    OpenScadBackend,
    render_fitted,
    section_wrapper,
)
from cad_photo_mcp.config import load_settings
from cad_photo_mcp.imaging import mask_bbox, silhouette_mask

SETTINGS = load_settings()


def openscad(**overrides):
    scad = SETTINGS.openscad
    if overrides:
        scad = type(scad)(**{**scad.__dict__, **overrides})
    return OpenScadBackend(scad, SETTINGS.render)


def test_section_wrapper_slices_the_mesh_at_z(tmp_path):
    text = section_wrapper(tmp_path / "m.stl", 4.5)
    assert "projection(cut = true)" in text
    assert "translate([0, 0, -4.5])" in text
    assert f'import("{(tmp_path / "m.stl").resolve()}")' in text


def test_section_wrapper_escapes_quotes_in_the_path(tmp_path):
    text = section_wrapper(tmp_path / 'we"ird.stl', 1.0)
    assert 'we\\"ird.stl' in text


def test_section_never_includes_the_source(tmp_path, monkeypatch):
    # Regression: the wrapper used `include <source>`, which also drew the
    # file's own top-level solid and silently replaced the 2D slice with it.
    source = tmp_path / "part.scad"
    source.write_text("module cover() { cube(10); }\ncover();\n")
    wrappers, commands = [], []

    def fake_run(cmd, **_):
        commands.append(cmd)
        target = Path(cmd[-1])
        if target.suffix == ".scad" and target != source:
            wrappers.append(target.read_text())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    openscad().section(source, tmp_path / "s.png", MODEL, 3.0, False, 60.0)

    assert len(wrappers) == 1
    assert "include <" not in wrappers[0]
    assert "use <" not in wrappers[0]
    assert "cover" not in wrappers[0]  # no assumed module name
    assert "import(" in wrappers[0]
    # the mesh export reads the source; the image render reads the wrapper
    assert commands[0][-1] == str(source)
    assert any(arg.startswith("--colorscheme=") for arg in commands[1])


def test_camera_centres_on_the_model_and_flips_from_below(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    backend = openscad()
    backend.render(tmp_path / "a.scad", tmp_path / "a.png", MODEL, False, 60.0)
    backend.render(tmp_path / "a.scad", tmp_path / "b.png", MODEL, True, 60.0)
    assert "--camera=5.0,3.0,0,0,0,0,60.0" in commands[0]
    assert "--camera=5.0,3.0,0,180,0,0,60.0" in commands[1]


def test_missing_executable_is_a_cad_error(tmp_path):
    backend = openscad(executable="definitely-not-openscad-xyz")
    assert not backend.available()
    with pytest.raises(CadError, match="not found"):
        backend.bounds(tmp_path / "a.scad")


def test_timeout_is_a_cad_error(tmp_path, monkeypatch):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(CadError, match="did not finish"):
        openscad(timeout_seconds=1.5).bounds(tmp_path / "a.scad")


def test_nonzero_exit_is_a_cad_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, "", "Parser error"),
    )
    with pytest.raises(CadError, match="Parser error"):
        openscad().bounds(tmp_path / "a.scad")


def test_render_fitted_widens_until_nothing_is_clipped(tmp_path):
    fake = FakeBackend(MODEL, SLICE)
    # start at 5mm across for a 20mm model: clipped until it grows past 20mm
    settings = type(SETTINGS.render)(
        **{**SETTINGS.render.__dict__, "initial_distance_factor": 0.25}
    )
    distance = render_fitted(
        fake, Path("x"), tmp_path / "r.png", MODEL, False, settings
    )
    assert distance > MODEL.width
    assert len(fake.distances) > 1
    assert fake.distances == sorted(fake.distances)


def test_render_fitted_gives_up_loudly(tmp_path):
    fake = FakeBackend(MODEL, SLICE)
    settings = type(SETTINGS.render)(
        **{
            **SETTINGS.render.__dict__,
            "initial_distance_factor": 0.01,
            "max_fit_attempts": 2,
        }
    )
    with pytest.raises(CadError, match="clipping"):
        render_fitted(fake, Path("x"), tmp_path / "r.png", MODEL, False, settings)


@pytest.mark.skipif(shutil.which("openscad") is None, reason="needs openscad")
def test_real_openscad_section_is_the_slice_not_the_solid(tmp_path):
    # A cone 40mm wide at the base, 16mm wide at z=8.
    source = tmp_path / "cone.scad"
    source.write_text("cylinder(h = 10, r1 = 20, r2 = 5, $fn = 96);\n")
    backend = openscad()
    model = backend.bounds(source)
    corners = (
        model.min_x,
        model.min_y,
        model.min_z,
        model.max_x,
        model.max_y,
        model.max_z,
    )
    assert corners == pytest.approx((-20, -20, 0, 20, 20, 10), abs=0.05)

    solid, section = tmp_path / "solid.png", tmp_path / "section.png"
    distance = render_fitted(backend, source, solid, model, False, SETTINGS.render)
    backend.section(source, section, model, 8.0, False, distance)

    solid_box = mask_bbox(silhouette_mask(Image.open(solid), backend.backdrop))
    section_box = mask_bbox(silhouette_mask(Image.open(section), backend.backdrop))
    px_per_mm = solid_box.width / model.width
    assert section_box.width / px_per_mm == pytest.approx(16.0, rel=0.03)
