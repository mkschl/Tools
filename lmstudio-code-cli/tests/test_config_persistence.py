from unittest.mock import patch

from lmstudio_code_cli.main import _load_config, _save_config


def test_load_config_returns_empty_when_file_missing(tmp_path):
    with patch("lmstudio_code_cli.main._CONFIG_PATH", tmp_path / "config.toml"):
        assert _load_config() == {}


def test_save_and_load_config_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    with patch("lmstudio_code_cli.main._CONFIG_PATH", path):
        _save_config({"theme": "dracula"})
        assert _load_config() == {"theme": "dracula"}


def test_save_config_writes_valid_toml(tmp_path):
    path = tmp_path / "config.toml"
    with patch("lmstudio_code_cli.main._CONFIG_PATH", path):
        _save_config({"theme": "nord"})
    assert 'theme = "nord"' in path.read_text()


def test_save_config_merges_with_existing(tmp_path):
    path = tmp_path / "config.toml"
    with patch("lmstudio_code_cli.main._CONFIG_PATH", path):
        _save_config({"theme": "gruvbox"})
        _save_config({"model": "gemma-4"})
        cfg = _load_config()
        assert cfg["theme"] == "gruvbox"
        assert cfg["model"] == "gemma-4"


def test_save_config_updates_existing_key(tmp_path):
    path = tmp_path / "config.toml"
    with patch("lmstudio_code_cli.main._CONFIG_PATH", path):
        _save_config({"theme": "gruvbox"})
        _save_config({"theme": "catppuccin"})
        assert _load_config()["theme"] == "catppuccin"


def test_save_config_handles_integer_values(tmp_path):
    path = tmp_path / "config.toml"
    with patch("lmstudio_code_cli.main._CONFIG_PATH", path):
        _save_config({"max_tokens": 4096})
        cfg = _load_config()
        assert cfg["max_tokens"] == 4096


def test_save_config_stores_url(tmp_path):
    path = tmp_path / "config.toml"
    with patch("lmstudio_code_cli.main._CONFIG_PATH", path):
        _save_config({"url": "http://192.168.1.50:1234/v1"})
        assert _load_config()["url"] == "http://192.168.1.50:1234/v1"
