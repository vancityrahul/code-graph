from __future__ import annotations
import asyncio
import traceback
from typing import Callable, Awaitable
from investigator.result import ToolResult
from investigator.context import RunContext


ToolHandler = Callable[[dict, RunContext], Awaitable[ToolResult]]


class ToolDispatcher:
    def __init__(self, handlers: dict[str, ToolHandler]):
        self._handlers = handlers

    async def dispatch(self, tool_uses: list[dict], ctx: RunContext) -> list[dict]:
        tasks = [self._run_one(tu, ctx) for tu in tool_uses]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def _run_one(self, tool_use: dict, ctx: RunContext) -> dict:
        tool_id = tool_use["id"]
        name = tool_use["name"]
        args = tool_use.get("input", {})

        handler = self._handlers.get(name)
        if not handler:
            result = ToolResult.error(f"Unknown tool: {name}", error_code="UNKNOWN_TOOL")
        else:
            try:
                result = await handler(args, ctx)
            except Exception as e:
                result = ToolResult.error(
                    f"{name} raised: {e}\n{traceback.format_exc()}",
                    error_code="TOOL_EXCEPTION",
                )

        return {
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": [result.to_mcp_content()],
        }
