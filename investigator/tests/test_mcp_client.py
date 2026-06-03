import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from investigator.tools.mcp_client import MCPClient


@pytest.fixture
def mock_process():
    proc = MagicMock()
    proc.stdin = AsyncMock()
    proc.stdout = AsyncMock()
    proc.returncode = None
    return proc


@pytest.mark.asyncio
async def test_call_tool_sends_correct_jsonrpc(mock_process):
    response_line = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": '{"ok": true, "data": "sym1"}'}]},
    }).encode() + b"\n"
    mock_process.stdout.readline = AsyncMock(return_value=response_line)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        client = MCPClient(server_path="/fake/path")
        await client.start()
        result = await client.call_tool("find_symbol", {"name": "auth"})

    assert result.ok is True
    assert "sym1" in result.data
    written = mock_process.stdin.write.call_args[0][0].decode()
    msg = json.loads(written.strip())
    assert msg["method"] == "tools/call"
    assert msg["params"]["name"] == "find_symbol"


@pytest.mark.asyncio
async def test_call_tool_returns_error_on_rpc_error(mock_process):
    response_line = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "symbol not found"},
    }).encode() + b"\n"
    mock_process.stdout.readline = AsyncMock(return_value=response_line)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        client = MCPClient(server_path="/fake/path")
        await client.start()
        result = await client.call_tool("find_symbol", {"name": "zzz"})

    assert result.ok is False
    assert "symbol not found" in result.error


@pytest.mark.asyncio
async def test_list_tools_returns_names(mock_process):
    response_line = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "find_symbol", "description": "...", "inputSchema": {}},
                {"name": "get_callers", "description": "...", "inputSchema": {}},
            ]
        },
    }).encode() + b"\n"
    mock_process.stdout.readline = AsyncMock(return_value=response_line)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        client = MCPClient(server_path="/fake/path")
        await client.start()
        tools = await client.list_tools()

    assert "find_symbol" in [t["name"] for t in tools]
    assert "get_callers" in [t["name"] for t in tools]
