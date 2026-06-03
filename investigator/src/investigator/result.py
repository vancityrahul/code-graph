from __future__ import annotations
import json


class ToolResult:
    """Unified result type for all investigator tools.

    ToolResult.ok(...) and ToolResult.error(...) are factory classmethods.
    On instances, r.ok is a bool and r.error is a str — Python resolves
    instance.__dict__ before class descriptors, so there is no collision.
    """

    def __init__(
        self,
        *,
        ok: bool,
        data: str = "",
        error: str = "",
        error_code: str = "",
        meta: dict | None = None,
    ) -> None:
        self.ok = ok
        self.data = data
        self.error = error
        self.error_code = error_code
        self.meta = meta if meta is not None else {}

    @classmethod
    def ok(cls, data: str, duration_ms: int = 0, truncated: bool = False) -> "ToolResult":
        return cls(
            ok=True,
            data=data,
            meta={"duration_ms": duration_ms, "truncated": truncated},
        )

    @classmethod
    def error(cls, message: str, error_code: str = "ERROR", duration_ms: int = 0) -> "ToolResult":
        return cls(
            ok=False,
            error=message,
            error_code=error_code,
            meta={"duration_ms": duration_ms, "truncated": False},
        )

    def to_mcp_content(self) -> dict:
        if self.ok:
            payload = {"ok": True, "data": self.data, "meta": self.meta}
        else:
            payload = {
                "ok": False,
                "error": self.error,
                "error_code": self.error_code,
                "meta": self.meta,
            }
        return {"type": "text", "text": json.dumps(payload)}
