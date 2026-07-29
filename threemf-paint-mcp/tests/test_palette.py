import json
import zipfile

import pytest

from threemf_paint_mcp.palette import PaletteError, swap_filament_palette
from threemf_paint_mcp.threemf_model import PROJECT_SETTINGS, ROOT_MODEL


def test_swap_filament_palette_changes_only_requested_slots(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    result = swap_filament_palette(single_object_3mf, {"2": "#123456"}, output)

    assert result["colours_changed"] == {"2": {"old": "#FF0000", "new": "#123456"}}
    assert result["validation"]["ok"] is True

    with zipfile.ZipFile(output) as zf:
        config = json.loads(zf.read(PROJECT_SETTINGS))
        assert config["filament_colour"] == [
            "#000000",
            "#123456",
            "#00FF00",
            "#0000FF",
        ]


def test_swap_filament_palette_normalizes_case(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    result = swap_filament_palette(single_object_3mf, {"1": "#abcdef"}, output)
    assert result["colours_changed"]["1"]["new"] == "#ABCDEF"


def test_swap_filament_palette_accepts_8_digit_hex(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    result = swap_filament_palette(single_object_3mf, {"1": "#11223344"}, output)
    assert result["colours_changed"]["1"]["new"] == "#11223344"


def test_swap_filament_palette_rejects_invalid_hex(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    with pytest.raises(PaletteError, match="invalid hex color"):
        swap_filament_palette(single_object_3mf, {"1": "orange"}, output)


def test_swap_filament_palette_rejects_unknown_slot(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    with pytest.raises(PaletteError, match="slots not found"):
        swap_filament_palette(single_object_3mf, {"99": "#123456"}, output)


def test_swap_filament_palette_already_correct_writes_nothing(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    result = swap_filament_palette(single_object_3mf, {"1": "#000000"}, output)

    assert result["already_correct"] is True
    assert result["output_path"] is None
    assert not output.exists()


def test_swap_filament_palette_preserves_other_zip_entries(single_object_3mf, tmp_path):
    output = tmp_path / "swapped.3mf"
    swap_filament_palette(single_object_3mf, {"2": "#123456"}, output)

    with zipfile.ZipFile(single_object_3mf) as original, zipfile.ZipFile(output) as new:
        assert set(original.namelist()) == set(new.namelist())
        assert original.read(ROOT_MODEL) == new.read(ROOT_MODEL)
