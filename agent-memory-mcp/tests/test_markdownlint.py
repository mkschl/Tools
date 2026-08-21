import json
from unittest.mock import patch

import agent_memory_mcp.markdownlint as markdownlint

_VIOLATION = {
    "fileName": "note.md",
    "lineNumber": 2,
    "ruleNames": ["MD018", "no-missing-space-atx"],
    "ruleDescription": "No space after hash on atx style heading",
    "errorDetail": None,
}


def _fake_run(returncode=0, stderr="", stdout=""):
    class Result:
        pass

    r = Result()
    r.returncode = returncode
    r.stderr = stderr
    r.stdout = stdout
    return r


# ── Binary missing ──────────────────────────────────────────────────────────


def test_fix_content_returns_unchanged_when_binary_missing():
    with patch("shutil.which", return_value=None):
        content, issues = markdownlint.fix_content("#Bad")
    assert content == "#Bad"
    assert issues == []


def test_format_issues_empty_list_reports_clean():
    assert markdownlint.format_issues([]) == "No markdownlint issues found."


# ── Fix mode ─────────────────────────────────────────────────────────────────


def test_fix_content_returns_fixed_file_contents_when_clean():
    fake = _fake_run(returncode=0)
    with (
        patch("shutil.which", return_value="/usr/bin/markdownlint"),
        patch("subprocess.run", return_value=fake),
    ):
        content, issues = markdownlint.fix_content("# Title\n\nText.\n")
    assert issues == []
    assert content == "# Title\n\nText.\n"


def test_fix_content_returns_remaining_issues_from_json_stderr():
    fake = _fake_run(returncode=1, stderr=json.dumps([_VIOLATION]))
    with (
        patch("shutil.which", return_value="/usr/bin/markdownlint"),
        patch("subprocess.run", return_value=fake) as mock_run,
    ):
        content, issues = markdownlint.fix_content("#Bad\n")

    assert issues == [_VIOLATION]
    assert content  # temp file round-tripped, content is a string
    assert "--fix" in mock_run.call_args[0][0]
    assert "--json" in mock_run.call_args[0][0]


def test_fix_content_handles_non_json_output():
    fake = _fake_run(returncode=1, stderr="not json")
    with (
        patch("shutil.which", return_value="/usr/bin/markdownlint"),
        patch("subprocess.run", return_value=fake),
    ):
        _, issues = markdownlint.fix_content("bad")
    assert issues[0]["ruleDescription"] == "not json"


# ── format_issues ────────────────────────────────────────────────────────────


def test_format_issues_renders_readable_report():
    report = markdownlint.format_issues([_VIOLATION])
    assert "Line 2" in report
    assert "MD018/no-missing-space-atx" in report
    assert "No space after hash" in report


# ── config file discovery ────────────────────────────────────────────────────


def test_fix_content_passes_config_flag_when_default_config_exists(tmp_path, monkeypatch):
    config = tmp_path / ".markdownlint.jsonc"
    config.write_text('{"default": true}')
    monkeypatch.setattr(markdownlint, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.delenv("AGENT_MEMORY_MARKDOWNLINT_CONFIG", raising=False)

    fake = _fake_run(returncode=0)
    with (
        patch("shutil.which", return_value="/usr/bin/markdownlint"),
        patch("subprocess.run", return_value=fake) as mock_run,
    ):
        markdownlint.fix_content("# Title\n")

    args = mock_run.call_args[0][0]
    assert "--config" in args
    assert str(config) in args


def test_fix_content_omits_config_flag_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(markdownlint, "DEFAULT_CONFIG_PATH", tmp_path / "missing.jsonc")
    monkeypatch.delenv("AGENT_MEMORY_MARKDOWNLINT_CONFIG", raising=False)

    fake = _fake_run(returncode=0)
    with (
        patch("shutil.which", return_value="/usr/bin/markdownlint"),
        patch("subprocess.run", return_value=fake) as mock_run,
    ):
        markdownlint.fix_content("# Title\n")

    assert "--config" not in mock_run.call_args[0][0]


def test_fix_content_env_override_takes_precedence(tmp_path, monkeypatch):
    default_config = tmp_path / "default.jsonc"
    default_config.write_text('{"default": true}')
    override_config = tmp_path / "custom.jsonc"
    override_config.write_text('{"default": true, "MD013": false}')
    monkeypatch.setattr(markdownlint, "DEFAULT_CONFIG_PATH", default_config)
    monkeypatch.setenv("AGENT_MEMORY_MARKDOWNLINT_CONFIG", str(override_config))

    fake = _fake_run(returncode=0)
    with (
        patch("shutil.which", return_value="/usr/bin/markdownlint"),
        patch("subprocess.run", return_value=fake) as mock_run,
    ):
        markdownlint.fix_content("# Title\n")

    args = mock_run.call_args[0][0]
    assert str(override_config) in args
    assert str(default_config) not in args
