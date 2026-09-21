from pathlib import Path

from cad_photo_mcp.config import CONFIG_PATH, load_settings


def test_shipped_config_loads_with_typed_values():
    settings = load_settings()
    assert settings.openscad.background == (255, 255, 229)
    assert settings.openscad.colorscheme == "Cornfield"
    assert settings.render.max_fit_attempts > 0
    assert settings.render.fit_growth > 1
    assert 0 < settings.calibration.cross_check_tolerance_pct < 10


def test_store_dir_expands_home(tmp_path):
    custom = tmp_path / "config.toml"
    custom.write_text(
        CONFIG_PATH.read_text().replace(
            'store_dir = "~/.cache/cad-photo-mcp/calibrations"',
            'store_dir = "~/elsewhere"',
        )
    )
    settings = load_settings(custom)
    assert settings.calibration.store_dir == Path.home() / "elsewhere"
