import pytest
from unittest.mock import MagicMock, patch
from investigator.tools.bedrock_memory import BedrockMemoryTool


@pytest.fixture
def mock_boto_client():
    client = MagicMock()
    return client


def test_memory_save_calls_boto(mock_boto_client):
    with patch("boto3.client", return_value=mock_boto_client):
        tool = BedrockMemoryTool(memory_id="mem-123", region="us-east-1")
        result = tool.save(
            user_id="user1",
            repo_path="/home/user/myrepo",
            content="Auth lives in pkg/identity/auth.py",
        )
    assert result.ok is True
    # AgentCore data-plane write goes through create_event
    assert mock_boto_client.create_event.called, "Expected create_event to be called"


def test_memory_search_returns_results(mock_boto_client):
    mock_boto_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"content": {"text": "Auth is in pkg/identity/auth.py"}}
        ]
    }
    with patch("boto3.client", return_value=mock_boto_client):
        tool = BedrockMemoryTool(memory_id="mem-123", region="us-east-1")
        result = tool.search(
            user_id="user1",
            repo_path="/home/user/myrepo",
            query="where is auth?",
        )
    assert result.ok is True
    assert "auth" in result.data.lower()
    assert mock_boto_client.retrieve_memory_records.called


def test_memory_save_handles_boto_error(mock_boto_client):
    mock_boto_client.create_event.side_effect = Exception("AccessDenied")
    with patch("boto3.client", return_value=mock_boto_client):
        tool = BedrockMemoryTool(memory_id="mem-123", region="us-east-1")
        result = tool.save(user_id="u", repo_path="/r", content="findings")
    assert result.ok is False
    assert result.error_code == "MEMORY_ERROR"


def test_memory_tool_schemas():
    with patch("boto3.client"):
        tool = BedrockMemoryTool(memory_id="mem-123", region="us-east-1")
    schemas = tool.tool_schemas()
    names = [s["name"] for s in schemas]
    assert "memory_save" in names
    assert "memory_search" in names
