from unittest.mock import MagicMock, patch

from lmstudio_code_cli.mcp_client import MCPGatewayClient, MCPTool


# ── MCPTool.to_openai ──────────────────────────────────────────────────────────

def test_to_openai_adds_missing_properties_and_type():
    tool = MCPTool(name="my_tool", description="does stuff", input_schema={})
    result = tool.to_openai()
    assert result["type"] == "function"
    params = result["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"] == {}


def test_to_openai_preserves_existing_properties():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    tool = MCPTool(name="t", description="", input_schema=schema)
    result = tool.to_openai()
    assert result["function"]["parameters"]["properties"] == {"x": {"type": "string"}}


def test_to_openai_name_and_description_forwarded():
    tool = MCPTool(name="list_files", description="Lists files", input_schema={})
    fn = tool.to_openai()["function"]
    assert fn["name"] == "list_files"
    assert fn["description"] == "Lists files"


def test_to_openai_none_schema_treated_as_empty():
    tool = MCPTool(name="t", description="", input_schema=None)
    params = tool.to_openai()["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"] == {}


# ── Fixture ────────────────────────────────────────────────────────────────────

def _make_client(tools=None):
    """Return an MCPGatewayClient without making any real HTTP calls."""
    with (
        patch("lmstudio_code_cli.mcp_client.httpx.Client"),
        patch.object(MCPGatewayClient, "_initialize"),
        patch.object(MCPGatewayClient, "_list_tools", return_value=tools or []),
    ):
        return MCPGatewayClient(url="http://test", token="tok")


# ── _parse_sse ─────────────────────────────────────────────────────────────────

def test_parse_sse_extracts_first_data_event():
    client = _make_client()
    sse = 'event: message\ndata: {"jsonrpc":"2.0","result":{"ok":true}}\n\n'
    assert client._parse_sse(sse) == {"jsonrpc": "2.0", "result": {"ok": True}}


def test_parse_sse_skips_done_sentinel_continues_to_next():
    client = _make_client()
    sse = 'data: [DONE]\ndata: {"jsonrpc":"2.0","result":{}}\n'
    assert client._parse_sse(sse) == {"jsonrpc": "2.0", "result": {}}


def test_parse_sse_returns_empty_dict_when_no_data_lines():
    client = _make_client()
    assert client._parse_sse("event: ping\n\n") == {}


def test_parse_sse_skips_invalid_json_continues_to_next():
    client = _make_client()
    sse = "data: {bad json}\ndata: {\"ok\": 1}\n"
    assert client._parse_sse(sse) == {"ok": 1}


# ── owns ───────────────────────────────────────────────────────────────────────

def test_owns_returns_true_for_registered_tool():
    tools = [MCPTool("decision_log", "", {}), MCPTool("kanban_add", "", {})]
    client = _make_client(tools=tools)
    assert client.owns("decision_log")
    assert client.owns("kanban_add")


def test_owns_returns_false_for_unknown_tool():
    client = _make_client()
    assert not client.owns("nonexistent_tool")


# ── call_tool ──────────────────────────────────────────────────────────────────

def test_call_tool_returns_text_content():
    client = _make_client()
    with patch.object(client, "_request", return_value={
        "content": [{"type": "text", "text": "hello from mcp"}]
    }):
        assert client.call_tool("some_tool", {}) == "hello from mcp"


def test_call_tool_image_content_replaced_with_placeholder():
    client = _make_client()
    with patch.object(client, "_request", return_value={
        "content": [{"type": "image", "data": "base64stuff"}]
    }):
        assert "[image content omitted]" in client.call_tool("screenshot", {})


def test_call_tool_unknown_type_json_dumped():
    client = _make_client()
    with patch.object(client, "_request", return_value={
        "content": [{"type": "blob", "data": "xyz"}]
    }):
        result = client.call_tool("t", {})
        assert "blob" in result


def test_call_tool_is_error_flag_prepends_message():
    client = _make_client()
    with patch.object(client, "_request", return_value={
        "isError": True,
        "content": [{"type": "text", "text": "something broke"}],
    }):
        result = client.call_tool("fail_tool", {})
        assert "error" in result.lower()
        assert "something broke" in result


def test_call_tool_empty_content_returns_placeholder():
    client = _make_client()
    with patch.object(client, "_request", return_value={"content": []}):
        assert client.call_tool("empty", {}) == "(empty response)"


def test_call_tool_multiple_text_items_joined():
    client = _make_client()
    with patch.object(client, "_request", return_value={
        "content": [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]
    }):
        result = client.call_tool("multi", {})
        assert "part one" in result
        assert "part two" in result


def test_call_tool_non_dict_content_item_stringified():
    client = _make_client()
    with patch.object(client, "_request", return_value={"content": ["raw string"]}):
        result = client.call_tool("t", {})
        assert "raw string" in result


# ── MCPGatewayClient.to_openai_tools ──────────────────────────────────────────

def test_to_openai_tools_returns_list_for_all_tools():
    tools = [MCPTool("a", "desc a", {}), MCPTool("b", "desc b", {"type": "object"})]
    client = _make_client(tools=tools)
    result = client.to_openai_tools()
    assert len(result) == 2
    names = {r["function"]["name"] for r in result}
    assert names == {"a", "b"}


def test_to_openai_tools_empty_when_no_tools():
    client = _make_client(tools=[])
    assert client.to_openai_tools() == []


# ── MCPGatewayClient._parse_response ──────────────────────────────────────────

def _make_resp(status=200, content_type="application/json", json_data=None, text="", content=b"x"):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.request = MagicMock()
    resp.request.content = b'{"method": "tools/list"}'
    return resp


def test_parse_response_returns_empty_on_202():
    client = _make_client()
    resp = _make_resp(status=202, content=b"")
    assert client._parse_response(resp) == {}


def test_parse_response_returns_empty_on_no_content():
    client = _make_client()
    resp = _make_resp(content=b"")
    assert client._parse_response(resp) == {}


def test_parse_response_extracts_result_from_json():
    client = _make_client()
    resp = _make_resp(json_data={"result": {"tools": []}})
    assert client._parse_response(resp) == {"tools": []}


def test_parse_response_extracts_result_from_sse():
    client = _make_client()
    sse_body = 'data: {"jsonrpc":"2.0","result":{"ok":true}}\n'
    resp = _make_resp(content_type="text/event-stream", text=sse_body)
    assert client._parse_response(resp) == {"ok": True}


def test_parse_response_raises_on_error_in_envelope():
    client = _make_client()
    resp = _make_resp(json_data={"error": {"code": -32600, "message": "Invalid request"}})
    import pytest
    with pytest.raises(RuntimeError, match="Invalid request"):
        client._parse_response(resp)
