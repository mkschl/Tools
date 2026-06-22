import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MCPConfig:
    url: str
    token: str

    @staticmethod
    def from_mcp_json(path: Path) -> "MCPConfig | None":
        """Read the first HTTP mcpgateway entry from a .mcp.json file."""
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        for _name, server in data.get("mcpServers", {}).items():
            url = server.get("url", "")
            if not url:
                continue
            auth = server.get("headers", {}).get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            return MCPConfig(url=url, token=token)
        return None


@dataclass
class Config:
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    model: str = ""
    cwd: str = field(default_factory=os.getcwd)
    max_tokens: int = 32768
    mcp: MCPConfig | None = None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1"),
            api_key=os.environ.get("LMSTUDIO_API_KEY", "lm-studio"),
            model=os.environ.get("LMSTUDIO_MODEL", ""),
            max_tokens=int(os.environ.get("LMSTUDIO_MAX_TOKENS", "32768")),
        )
