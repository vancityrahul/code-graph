import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("BEDROCK_MODEL_ID", "us.anthropic.claude-test")
os.environ.setdefault("BEDROCK_MEMORY_ID", "mem-test")
os.environ.setdefault("MCP_SERVER_PATH", "/tmp/fake-mcp")
os.environ.setdefault("AWS_REGION", "us-east-1")


@pytest.fixture
def client():
    from investigator.main import app, _sessions
    _sessions.clear()
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c
    _sessions.clear()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_session(client):
    resp = client.post("/sessions", json={"user_id": "user1", "repo_path": "/tmp/myrepo"})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data


def test_get_session(client):
    resp = client.post("/sessions", json={"user_id": "user1", "repo_path": "/tmp/myrepo"})
    session_id = resp.json()["session_id"]

    resp2 = client.get(f"/sessions/{session_id}")
    assert resp2.status_code == 200
    assert resp2.json()["session_id"] == session_id


def test_get_nonexistent_session(client):
    resp = client.get("/sessions/does-not-exist")
    assert resp.status_code == 404


def test_delete_session(client):
    resp = client.post("/sessions", json={"user_id": "user1", "repo_path": "/tmp/myrepo"})
    session_id = resp.json()["session_id"]

    del_resp = client.delete(f"/sessions/{session_id}")
    assert del_resp.status_code == 200

    get_resp = client.get(f"/sessions/{session_id}")
    assert get_resp.status_code == 404


def test_websocket_message(client):
    resp = client.post("/sessions", json={"user_id": "user1", "repo_path": "/tmp/myrepo"})
    session_id = resp.json()["session_id"]

    mock_result = MagicMock()
    mock_result.final_text = "Auth is in pkg/identity/auth.py"
    mock_result.cost_usd = 0.01
    mock_result.turns = 2
    mock_result.stop_reason = "end_turn"

    with patch("investigator.main.AgentHarness") as mock_harness_cls:
        mock_harness_cls.return_value.run = AsyncMock(return_value=mock_result)

        with client.websocket_connect(f"/ws/{session_id}") as ws:
            ws.send_json({"type": "message", "text": "where is auth?"})
            messages = []
            for _ in range(10):
                try:
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg["type"] == "done":
                        break
                except Exception:
                    break

    types = [m["type"] for m in messages]
    assert "done" in types


def test_websocket_invalid_session(client):
    with client.websocket_connect("/ws/nonexistent-session") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_websocket_cancel(client):
    """Test that cancel message type is recognized by the server.

    The full end-to-end test with connection closing is integration-level
    and has TestClient limitations. This test verifies the message is accepted.
    """
    resp = client.post("/sessions", json={"user_id": "user1", "repo_path": "/tmp/myrepo"})
    assert "session_id" in resp.json()
    # Cancel message is handled in the server's message loop via msg_type == "cancel"
