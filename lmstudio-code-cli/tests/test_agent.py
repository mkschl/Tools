from unittest.mock import MagicMock, patch

from lmstudio_code_cli.agent import _load_claude_md, Agent
from lmstudio_code_cli.config import Config


# ── _load_claude_md ────────────────────────────────────────────────────────────

def test_load_claude_md_returns_content_when_file_exists(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Project rules\n\nDo the thing.\n")
    result = _load_claude_md(str(tmp_path))
    assert "Project rules" in result
    assert "Do the thing." in result


def test_load_claude_md_returns_empty_string_when_missing(tmp_path):
    assert _load_claude_md(str(tmp_path)) == ""


def test_load_claude_md_returns_empty_string_on_permission_error(tmp_path):
    p = tmp_path / "CLAUDE.md"
    p.write_text("secret")
    p.chmod(0o000)
    try:
        result = _load_claude_md(str(tmp_path))
        assert result == ""
    finally:
        p.chmod(0o644)


# ── Agent._system_message ──────────────────────────────────────────────────────

def _make_agent(cwd: str, claude_md: str = "") -> Agent:
    cfg = Config(base_url="http://test", api_key="x", model="test-model", cwd=cwd)
    with (
        patch("lmstudio_code_cli.agent.OpenAI"),
        patch("lmstudio_code_cli.agent._load_claude_md", return_value=claude_md),
    ):
        return Agent(config=cfg)


def test_system_message_includes_working_directory(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    msg = agent._system_message()
    assert str(tmp_path) in msg["content"]


def test_system_message_includes_testing_rules(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    content = agent._system_message()["content"]
    assert "Testing" in content
    assert "pytest" in content
    assert "go test" in content


def test_system_message_appends_claude_md_when_present(tmp_path):
    agent = _make_agent(cwd=str(tmp_path), claude_md="# My project rules\nDo X not Y.\n")
    content = agent._system_message()["content"]
    assert "My project rules" in content
    assert "Do X not Y." in content


def test_system_message_omits_claude_md_section_when_empty(tmp_path):
    agent = _make_agent(cwd=str(tmp_path), claude_md="")
    content = agent._system_message()["content"]
    assert "Project instructions" not in content


def test_system_message_role_is_system(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    assert agent._system_message()["role"] == "system"
