import json

from lmstudio_code_cli.config import Config, MCPConfig


# ── MCPConfig.from_mcp_json ────────────────────────────────────────────────────

def test_from_mcp_json_parses_url_and_token(tmp_path):
    data = {
        "mcpServers": {
            "gateway": {
                "url": "http://localhost:8000/mcp",
                "headers": {"Authorization": "Bearer tok123"},
            }
        }
    }
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps(data))
    cfg = MCPConfig.from_mcp_json(p)
    assert cfg is not None
    assert cfg.url == "http://localhost:8000/mcp"
    assert cfg.token == "tok123"


def test_from_mcp_json_no_url_returns_none(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({"mcpServers": {"svc": {"type": "stdio"}}}))
    assert MCPConfig.from_mcp_json(p) is None


def test_from_mcp_json_empty_mcpservers_returns_none(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({"mcpServers": {}}))
    assert MCPConfig.from_mcp_json(p) is None


def test_from_mcp_json_missing_file_returns_none(tmp_path):
    assert MCPConfig.from_mcp_json(tmp_path / "nonexistent.json") is None


def test_from_mcp_json_invalid_json_returns_none(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text("{not valid json")
    assert MCPConfig.from_mcp_json(p) is None


def test_from_mcp_json_strips_bearer_prefix(tmp_path):
    data = {
        "mcpServers": {
            "gw": {
                "url": "http://host/mcp",
                "headers": {"Authorization": "Bearer   spaced-token  "},
            }
        }
    }
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps(data))
    cfg = MCPConfig.from_mcp_json(p)
    assert cfg is not None
    assert cfg.token == "spaced-token"


def test_from_mcp_json_no_auth_header_gives_empty_token(tmp_path):
    data = {"mcpServers": {"gw": {"url": "http://host/mcp"}}}
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps(data))
    cfg = MCPConfig.from_mcp_json(p)
    assert cfg is not None
    assert cfg.url == "http://host/mcp"
    assert cfg.token == ""


# ── Config.from_env ────────────────────────────────────────────────────────────

def test_config_from_env_uses_defaults(monkeypatch):
    for var in ("LMSTUDIO_URL", "LMSTUDIO_API_KEY", "LMSTUDIO_MODEL", "LMSTUDIO_MAX_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config.from_env()
    assert cfg.base_url == "http://localhost:1234/v1"
    assert cfg.api_key == "lm-studio"
    assert cfg.model == ""
    assert cfg.max_tokens == 32768


def test_config_from_env_respects_overrides(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_URL", "http://myserver:5678/v1")
    monkeypatch.setenv("LMSTUDIO_API_KEY", "mykey")
    monkeypatch.setenv("LMSTUDIO_MODEL", "llama-3.1-8b")
    monkeypatch.setenv("LMSTUDIO_MAX_TOKENS", "4096")
    cfg = Config.from_env()
    assert cfg.base_url == "http://myserver:5678/v1"
    assert cfg.api_key == "mykey"
    assert cfg.model == "llama-3.1-8b"
    assert cfg.max_tokens == 4096
