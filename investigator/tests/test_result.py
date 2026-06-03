from investigator.result import ToolResult


def test_ok_result_shape():
    r = ToolResult.ok(data="hello", duration_ms=10)
    assert r.ok is True
    assert r.data == "hello"
    assert r.meta["duration_ms"] == 10
    assert r.meta["truncated"] is False


def test_ok_result_truncated():
    r = ToolResult.ok(data="x" * 5000, truncated=True, duration_ms=5)
    assert r.meta["truncated"] is True


def test_error_result_shape():
    r = ToolResult.error("file not found", error_code="FILE_NOT_FOUND")
    assert r.ok is False
    assert r.error == "file not found"
    assert r.error_code == "FILE_NOT_FOUND"


def test_to_mcp_content():
    r = ToolResult.ok(data="result text")
    content = r.to_mcp_content()
    assert content["type"] == "text"
    assert "result text" in content["text"]


def test_error_to_mcp_content():
    r = ToolResult.error("bad input", error_code="INVALID")
    content = r.to_mcp_content()
    assert content["type"] == "text"
    assert "bad input" in content["text"]
