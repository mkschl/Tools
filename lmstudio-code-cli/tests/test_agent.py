import json
from unittest.mock import MagicMock, patch

from openai import APIConnectionError

from lmstudio_code_cli.agent import _load_claude_md, _format_api_error, Agent
from lmstudio_code_cli.config import Config
from lmstudio_code_cli.mcp_client import MCPTool


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


# ── Agent.enable_mcp_tools ─────────────────────────────────────────────────────

def _make_agent_with_mcp(tmp_path, mcp_tools: list[MCPTool]) -> Agent:
    cfg = Config(base_url="http://test", api_key="x", model="test-model", cwd=str(tmp_path))
    mcp = MagicMock()
    mcp.tools = mcp_tools
    with (
        patch("lmstudio_code_cli.agent.OpenAI"),
        patch("lmstudio_code_cli.agent._load_claude_md", return_value=""),
    ):
        return Agent(config=cfg, mcp=mcp)


def test_enable_mcp_tools_starts_with_no_mcp_tools(tmp_path):
    agent = _make_agent_with_mcp(tmp_path, [MCPTool("kubectl_get", "", {})])
    from lmstudio_code_cli.tools import ALL_TOOLS
    assert len(agent._tools) == len(ALL_TOOLS)


def test_enable_mcp_tools_adds_matching_tools(tmp_path):
    tools = [MCPTool("kubectl_get", "", {}), MCPTool("kanban_add", "", {})]
    agent = _make_agent_with_mcp(tmp_path, tools)
    count = agent.enable_mcp_tools(["kubectl"])
    assert count == 1
    names = {t["function"]["name"] for t in agent._tools}
    assert "kubectl_get" in names
    assert "kanban_add" not in names


def test_enable_mcp_tools_replaces_previous_selection(tmp_path):
    tools = [MCPTool("kubectl_get", "", {}), MCPTool("docker_run", "", {})]
    agent = _make_agent_with_mcp(tmp_path, tools)
    agent.enable_mcp_tools(["kubectl"])
    agent.enable_mcp_tools(["docker"])
    names = {t["function"]["name"] for t in agent._tools}
    assert "docker_run" in names
    assert "kubectl_get" not in names


def test_enable_mcp_tools_off_removes_all(tmp_path):
    tools = [MCPTool("kubectl_get", "", {}), MCPTool("kanban_add", "", {})]
    agent = _make_agent_with_mcp(tmp_path, tools)
    agent.enable_mcp_tools(["kubectl", "kanban"])
    count = agent.enable_mcp_tools([])
    assert count == 0
    from lmstudio_code_cli.tools import ALL_TOOLS
    assert len(agent._tools) == len(ALL_TOOLS)


def test_enable_mcp_tools_returns_zero_when_no_mcp(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    assert agent.enable_mcp_tools(["kubectl"]) == 0


def test_active_mcp_tool_names_reflects_enabled_tools(tmp_path):
    tools = [MCPTool("kubectl_get", "", {}), MCPTool("kanban_add", "", {})]
    agent = _make_agent_with_mcp(tmp_path, tools)
    agent.enable_mcp_tools(["kubectl"])
    assert agent.active_mcp_tool_names() == {"kubectl_get"}


def test_active_mcp_tool_names_empty_before_enable(tmp_path):
    tools = [MCPTool("kubectl_get", "", {})]
    agent = _make_agent_with_mcp(tmp_path, tools)
    assert agent.active_mcp_tool_names() == set()


# ── Agent.clear_history ────────────────────────────────────────────────────────

def test_clear_history_empties_history(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history.append({"role": "user", "content": "hello"})
    agent.history.append({"role": "assistant", "content": "hi"})
    agent.clear_history()
    assert agent.history == []


def test_clear_history_on_empty_history_is_safe(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.clear_history()
    assert agent.history == []


# ── Agent._execute ─────────────────────────────────────────────────────────────

def test_execute_routes_to_mcp_when_owned(tmp_path):
    agent = _make_agent_with_mcp(tmp_path, [MCPTool("kanban_add", "", {})])
    agent.mcp.owns.return_value = True
    agent.mcp.call_tool.return_value = "mcp result"
    result = agent._execute("kanban_add", {"board": "x"})
    agent.mcp.call_tool.assert_called_once_with("kanban_add", {"board": "x"})
    assert result == "mcp result"


def test_execute_routes_to_builtin_when_not_owned(tmp_path):
    agent = _make_agent_with_mcp(tmp_path, [])
    agent.mcp.owns.return_value = False
    with patch("lmstudio_code_cli.agent.execute_tool", return_value="builtin result") as mock_exec:
        result = agent._execute("read_file", {"path": "x.py"})
    mock_exec.assert_called_once_with("read_file", {"path": "x.py"}, agent.config.cwd)
    assert result == "builtin result"


def test_execute_routes_to_builtin_when_no_mcp(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    with patch("lmstudio_code_cli.agent.execute_tool", return_value="builtin result") as mock_exec:
        result = agent._execute("run_bash", {"command": "ls"})
    mock_exec.assert_called_once()
    assert result == "builtin result"


def test_execute_returns_error_string_on_mcp_exception(tmp_path):
    agent = _make_agent_with_mcp(tmp_path, [MCPTool("bad_tool", "", {})])
    agent.mcp.owns.return_value = True
    agent.mcp.call_tool.side_effect = RuntimeError("gateway down")
    result = agent._execute("bad_tool", {})
    assert "gateway down" in result


# ── Agent.pop_last_user_message ────────────────────────────────────────────────

def test_pop_last_user_message_returns_text_and_removes_turn(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    msg = agent.pop_last_user_message()
    assert msg == "hello"
    assert agent.history == []


def test_pop_last_user_message_removes_only_from_last_user_onwards(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "response one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "response two"},
    ]
    msg = agent.pop_last_user_message()
    assert msg == "second"
    assert len(agent.history) == 2
    assert agent.history[-1]["content"] == "response one"


def test_pop_last_user_message_returns_none_on_empty_history(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    assert agent.pop_last_user_message() is None


def test_pop_last_user_message_extracts_text_from_multipart_content(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": [{"type": "text", "text": "my question"}, {"type": "image_url", "image_url": {}}]},
    ]
    msg = agent.pop_last_user_message()
    assert msg == "my question"


# ── Agent.save_history / load_history ─────────────────────────────────────────

def test_save_and_load_history_round_trips(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    path = str(tmp_path / "conv.json")
    agent.save_history(path)
    agent.history = []
    count = agent.load_history(path)
    assert count == 2
    assert agent.history[0]["content"] == "hello"
    assert agent.history[1]["content"] == "world"


def test_load_history_returns_message_count(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [{"role": "user", "content": "a"}] * 5
    path = str(tmp_path / "conv.json")
    agent.save_history(path)
    agent.clear_history()
    assert agent.load_history(path) == 5


# ── Agent.estimated_tokens ─────────────────────────────────────────────────────

def test_estimated_tokens_zero_on_empty_history(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    assert agent.estimated_tokens == 0


def test_estimated_tokens_grows_with_history(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [{"role": "user", "content": "a" * 400}]
    assert agent.estimated_tokens == 100


def test_estimated_tokens_handles_multipart_content(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": [{"type": "text", "text": "a" * 200}]},
    ]
    assert agent.estimated_tokens == 50


def test_estimated_tokens_counts_tool_result_messages(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "x", "content": "b" * 300},
    ]
    assert agent.estimated_tokens == 100


def test_estimated_tokens_accumulates_across_turns(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.history = [
        {"role": "user", "content": "a" * 200},
        {"role": "assistant", "content": "b" * 200},
    ]
    assert agent.estimated_tokens == 100


# ── _format_api_error ──────────────────────────────────────────────────────────

def _make_api_error(status_code: int, body=None, message: str = "error"):
    exc = MagicMock()
    exc.status_code = status_code
    exc.message = message
    exc.body = body
    return exc


def test_format_api_error_includes_status_code():
    exc = _make_api_error(422, body={"error": "bad request"})
    result = _format_api_error(exc, "http://localhost:1234/v1", "gemma-4")
    assert "422" in result


def test_format_api_error_includes_endpoint():
    exc = _make_api_error(500, body=None, message="internal error")
    result = _format_api_error(exc, "http://localhost:1234/v1", "gemma-4")
    assert "localhost:1234" in result
    assert "chat/completions" in result


def test_format_api_error_includes_model():
    exc = _make_api_error(400, body=None, message="bad model")
    result = _format_api_error(exc, "http://localhost:1234/v1", "my-model")
    assert "my-model" in result


def test_format_api_error_parses_json_validation_list():
    error_list = json.dumps([{"path": ["tools", 0], "message": "field required"}])
    exc = _make_api_error(422, body={"error": error_list})
    result = _format_api_error(exc, "http://localhost:1234/v1", "m")
    assert "field required" in result
    assert "tools" in result


def test_format_api_error_falls_back_to_message_on_plain_string():
    exc = _make_api_error(503, body={"error": "service unavailable"})
    result = _format_api_error(exc, "http://localhost:1234/v1", "m")
    assert "service unavailable" in result


# ── Agent.list_models ──────────────────────────────────────────────────────────

def test_list_models_returns_model_ids(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    m1, m2 = MagicMock(id="gemma-4"), MagicMock(id="mistral-7b")
    agent.client.models.list.return_value.data = [m1, m2]
    assert agent.list_models() == ["gemma-4", "mistral-7b"]


def test_list_models_returns_empty_list_on_connection_error(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    agent.client.models.list.side_effect = APIConnectionError.__new__(APIConnectionError)
    assert agent.list_models() == []


# ── Agent.load_history error cases ────────────────────────────────────────────

def test_load_history_raises_on_missing_file(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    import pytest
    with pytest.raises(OSError):
        agent.load_history(str(tmp_path / "nonexistent.json"))


def test_load_history_raises_on_invalid_json(tmp_path):
    agent = _make_agent(cwd=str(tmp_path))
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    import pytest
    with pytest.raises(ValueError):
        agent.load_history(str(bad))
