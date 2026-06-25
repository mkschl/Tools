from lmstudio_code_cli.defaults import DEFAULTS
from lmstudio_code_cli.themes import DEFAULT_THEME, THEMES


# ── DEFAULTS structure ─────────────────────────────────────────────────────────

def test_defaults_contains_all_expected_keys():
    assert "url" in DEFAULTS
    assert "api_key" in DEFAULTS
    assert "max_tokens" in DEFAULTS
    assert "theme" in DEFAULTS
    assert "history_file" in DEFAULTS
    assert "conversation_file" in DEFAULTS


def test_defaults_url_is_string():
    assert isinstance(DEFAULTS["url"], str)
    assert DEFAULTS["url"].startswith("http")


def test_defaults_api_key_is_string():
    assert isinstance(DEFAULTS["api_key"], str)
    assert DEFAULTS["api_key"]


def test_defaults_max_tokens_is_integer():
    assert isinstance(DEFAULTS["max_tokens"], int)
    assert DEFAULTS["max_tokens"] > 0


def test_defaults_theme_is_string():
    assert isinstance(DEFAULTS["theme"], str)


def test_defaults_history_file_is_string():
    assert isinstance(DEFAULTS["history_file"], str)


def test_defaults_conversation_file_is_string():
    assert isinstance(DEFAULTS["conversation_file"], str)


# ── Cross-file consistency ─────────────────────────────────────────────────────

def test_default_theme_exists_in_themes():
    assert DEFAULT_THEME in THEMES


def test_default_theme_matches_defaults_toml():
    assert DEFAULT_THEME == DEFAULTS["theme"]
