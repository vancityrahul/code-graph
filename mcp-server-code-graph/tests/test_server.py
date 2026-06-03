"""
stdio round-trip integration tests.

Spawns the MCP server as a subprocess, sends newline-delimited JSON-RPC
messages over stdin, reads responses from stdout.

mcp 1.27.1 uses newline-delimited JSON (not Content-Length framing).
"""
import json
import select
import subprocess
import sys
import time
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "python_sample"
SERVER_MODULE = "code_graph_mcp.server"


def _make_msg(method: str, params: dict, id_: int = 1) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}) + "\n").encode()


def _make_notif(method: str, params: dict) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode()


def _read_msg(proc, timeout: float = 10.0) -> dict:
    """Read the next JSON-RPC response (skips notifications without an id)."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for server response")
        r, _, _ = select.select([proc.stdout], [], [], remaining)
        if not r:
            raise TimeoutError("Timed out waiting for server response")
        line = proc.stdout.readline()
        if not line:
            raise EOFError("Server closed stdout")
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        # Skip notifications (they have no 'id')
        if "id" in msg:
            return msg


@pytest.fixture(scope="module")
def server_proc(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("srv")
    db_path = str(tmp / "graph.kuzu")

    proc = subprocess.Popen(
        [sys.executable, "-m", SERVER_MODULE, "--db-path", db_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent.parent),  # mcp-server-code-graph/
    )

    # Initialize
    proc.stdin.write(_make_msg("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    }, id_=1))
    proc.stdin.flush()
    _read_msg(proc)  # consume initialize response

    # Send initialized notification (no response expected)
    proc.stdin.write(_make_notif("notifications/initialized", {}))
    proc.stdin.flush()

    yield proc, db_path

    proc.terminate()
    proc.wait(timeout=5)


def test_server_lists_all_tools(server_proc):
    proc, _ = server_proc
    proc.stdin.write(_make_msg("tools/list", {}, id_=2))
    proc.stdin.flush()
    resp = _read_msg(proc)
    tool_names = {t["name"] for t in resp["result"]["tools"]}
    for expected in (
        "index_repo", "index_status", "find_symbol", "get_file_summary",
        "get_callers", "get_callees", "find_path", "find_references",
        "get_imports", "get_test_coverage", "query_graph",
    ):
        assert expected in tool_names, f"Missing tool: {expected}"


def test_server_index_repo_tool(server_proc):
    proc, db_path = server_proc
    proc.stdin.write(_make_msg("tools/call", {
        "name": "index_repo",
        "arguments": {"path": str(FIXTURE.resolve()), "db_path": db_path},
    }, id_=3))
    proc.stdin.flush()
    resp = _read_msg(proc)
    content = resp["result"]["content"][0]["text"]
    d = json.loads(content)
    assert d["ok"] is True
    assert d["data"]["file_count"] >= 2


def test_server_find_symbol_tool(server_proc):
    proc, db_path = server_proc
    proc.stdin.write(_make_msg("tools/call", {
        "name": "find_symbol",
        "arguments": {"name": "AuthManager", "db_path": db_path},
    }, id_=4))
    proc.stdin.flush()
    resp = _read_msg(proc)
    content = json.loads(resp["result"]["content"][0]["text"])
    assert content["ok"] is True
    assert len(content["data"]) >= 1
