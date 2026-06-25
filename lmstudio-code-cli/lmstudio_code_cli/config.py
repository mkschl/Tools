import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .defaults import DEFAULTS


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
    base_url: str = field(default_factory=lambda: DEFAULTS["url"])
    api_key: str = field(default_factory=lambda: DEFAULTS["api_key"])
    model: str = ""
    cwd: str = field(default_factory=os.getcwd)
    max_tokens: int = field(default_factory=lambda: DEFAULTS["max_tokens"])
    mcp: MCPConfig | None = None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.environ.get("LMSTUDIO_URL", DEFAULTS["url"]),
            api_key=os.environ.get("LMSTUDIO_API_KEY", DEFAULTS["api_key"]),
            model=os.environ.get("LMSTUDIO_MODEL", ""),
            max_tokens=int(os.environ.get("LMSTUDIO_MAX_TOKENS", DEFAULTS["max_tokens"])),
        )
