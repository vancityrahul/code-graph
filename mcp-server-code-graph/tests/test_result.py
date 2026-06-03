import json
from code_graph_mcp.result import ToolResult


def test_ok_result_has_correct_shape():
    r = ToolResult.ok(data="hello", meta={"duration_ms": 5})
    d = json.loads(r.to_json())
    assert d["ok"] is True
    assert d["data"] == "hello"
    assert d["meta"]["duration_ms"] == 5
    assert "tool_version" in d["meta"]


def test_error_result_has_correct_shape():
    r = ToolResult.error("not found", code="FILE_NOT_FOUND")
    d = json.loads(r.to_json())
    assert d["ok"] is False
    assert d["error"] == "not found"
    assert d["error_code"] == "FILE_NOT_FOUND"
    assert d["meta"]["tool_version"] == "1"
    assert d["meta"]["truncated"] is False


def test_ok_with_truncation_flag():
    r = ToolResult.ok(data="x" * 5000, truncated=True)
    d = json.loads(r.to_json())
    assert d["meta"]["truncated"] is True
