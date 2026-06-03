import json
from typing import Any

TOOL_VERSION = "1"


class ToolResult:
    """Normalizes all tool output to one of two JSON shapes: ok or error."""

    def __init__(
        self,
        success: bool,
        data: Any = None,
        error: str = "",
        error_code: str = "",
        truncated: bool = False,
        extra_meta: dict | None = None,
    ) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.error_code = error_code
        self.truncated = truncated
        self.extra_meta = extra_meta or {}

    @classmethod
    def ok(cls, data: Any, meta: dict | None = None, truncated: bool = False) -> "ToolResult":
        return cls(success=True, data=data, truncated=truncated, extra_meta=meta or {})

    @classmethod
    def error(cls, message: str, code: str = "ERROR") -> "ToolResult":
        return cls(success=False, error=message, error_code=code)

    def to_json(self) -> str:
        if self.success:
            return json.dumps({
                "ok": True,
                "data": self.data,
                "meta": {
                    "truncated": self.truncated,
                    "tool_version": TOOL_VERSION,
                    **self.extra_meta,
                },
            })
        return json.dumps({
            "ok": False,
            "error": self.error,
            "error_code": self.error_code,
            "meta": {"tool_version": TOOL_VERSION, "truncated": False},
        })
