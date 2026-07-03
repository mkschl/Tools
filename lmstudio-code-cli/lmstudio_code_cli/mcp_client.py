"""Minimal synchronous MCP client over the Streamable HTTP transport.

Implements just enough of the MCP 2025-03-26 spec to discover and call tools
from an HTTP MCP gateway (initialize handshake, tools/list, tools/call).
"""

import json
from dataclasses import dataclass

import httpx


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict | None

    def to_openai(self) -> dict:
        """Return an OpenAI-compatible function-tool definition."""
        schema = dict(self.input_schema) if self.input_schema else {}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class MCPGatewayClient:
    """Synchronous MCP gateway client.

    Establishes an MCP session with the gateway over HTTP, discovers all available
    tools at construction time, and executes tool calls on demand.
    """

    def __init__(self, url: str, token: str, timeout: float = 60.0) -> None:
        self._url = url
        self._timeout = timeout
        self._req_id = 0
        self._session_id: str | None = None
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self.tools: list[MCPTool] = []
        self._tool_names: set[str] = set()

        self._initialize()
        self.tools = self._list_tools()
        self._tool_names = {t.name for t in self.tools}

    # ── Public API ────────────────────────────────────────────────────────────

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._tool_names

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                match item.get("type"):
                    case "text":
                        parts.append(item.get("text", ""))
                    case "image":
                        parts.append("[image content omitted]")
                    case _:
                        parts.append(json.dumps(item))
            else:
                parts.append(str(item))
        if result.get("isError"):
            return "Tool returned an error:\n" + "\n".join(parts)
        return "\n".join(parts) or "(empty response)"

    def to_openai_tools(self) -> list[dict]:
        return [t.to_openai() for t in self.tools]

    def close(self) -> None:
        self._client.close()

    # ── MCP protocol ──────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _base_headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post_raw(self, payload: dict) -> httpx.Response:
        try:
            return self._client.post(self._url, json=payload, headers=self._base_headers())
        except httpx.ConnectError as exc:
            raise RuntimeError(f"Cannot reach MCP gateway at {self._url}\n  {exc}") from exc
        except httpx.TimeoutException as exc:
            method = payload.get("method", "?")
            raise RuntimeError(f"MCP gateway timed out ({self._url}  {method})") from exc

    def _parse_response(self, resp: httpx.Response) -> dict:
        """Extract the JSON-RPC result dict from a response (JSON or SSE)."""
        if resp.status_code in (202, 204) or not resp.content:
            return {}

        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            envelope = self._parse_sse(resp.text)
        else:
            envelope = resp.json()

        if "error" in envelope:
            err = envelope["error"]
            method = resp.request.content and json.loads(resp.request.content).get("method", "?")
            raise RuntimeError(
                f"MCP gateway error on {method!r} ({self._url})\n"
                f"  code    : {err.get('code', '?')}\n"
                f"  message : {err.get('message', err)}"
            )

        return envelope.get("result", {})

    def _parse_sse(self, text: str) -> dict:
        """Extract the first data event from an SSE body."""
        for line in text.splitlines():
            if line.startswith("data: "):
                data = line[6:].strip()
                if data and data != "[DONE]":
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        continue
        return {}

    def _initialize(self) -> None:
        resp = self._post_raw({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "code-cli", "version": "0.1.0"},
            },
        })
        if not resp.is_success:
            raise RuntimeError(
                f"MCP gateway initialization failed ({self._url})\n"
                f"  HTTP {resp.status_code}: {resp.text[:200]}"
            )

        if sid := resp.headers.get("mcp-session-id"):
            self._session_id = sid

        # Send initialized notification (server may return 202/204)
        notif_resp = self._post_raw({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        # Tolerate any 2xx — notification responses carry no data
        if notif_resp.status_code >= 400:
            notif_resp.raise_for_status()

    def _request(self, method: str, params: dict) -> dict:
        resp = self._post_raw({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        })
        if not resp.is_success:
            raise RuntimeError(
                f"MCP gateway error on {method!r} ({self._url})\n"
                f"  HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return self._parse_response(resp)

    def _list_tools(self) -> list[MCPTool]:
        result = self._request("tools/list", {})
        tools = []
        for t in result.get("tools", []):
            tools.append(MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            ))
        return tools
