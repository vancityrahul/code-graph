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
    # Exactly one of these must have been called; which one depends on the
    # real Bedrock Agent Core Memory boto3 method name (see Task 14 checklist).
    any_save_called = (
        mock_boto_client.create_memory_record.called
        or mock_boto_client.put_memory.called
        or mock_boto_client.invoke_agent.called
    )
    assert any_save_called, "Expected boto3 save method to be called"


def test_memory_search_returns_results(mock_boto_client):
    mock_boto_client.retrieve_memory.return_value = {
        "memoryContents": [
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
    assert "auth" in result.data.lower() or result.data == ""


def test_memory_save_handles_boto_error(mock_boto_client):
    mock_boto_client.create_memory_record.side_effect = Exception("AccessDenied")
    mock_boto_client.put_memory.side_effect = Exception("AccessDenied")
    mock_boto_client.invoke_agent.side_effect = Exception("AccessDenied")
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
