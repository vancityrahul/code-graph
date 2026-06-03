import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from investigator.dispatcher import ToolDispatcher
from investigator.result import ToolResult
from investigator.context import RunContext


def make_ctx():
    return RunContext(agent_name="test", system_prompt="", tools=[], session_id="s1")


def make_tool_use(tool_id: str, name: str, args: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


@pytest.mark.asyncio
async def test_dispatcher_calls_registered_tool():
    handler = AsyncMock(return_value=ToolResult.ok(data="result"))
    dispatcher = ToolDispatcher({"read_file": handler})
    ctx = make_ctx()

    tool_uses = [make_tool_use("t1", "read_file", {"path": "/foo.py"})]
    results = await dispatcher.dispatch(tool_uses, ctx)

    handler.assert_called_once_with({"path": "/foo.py"}, ctx)
    assert len(results) == 1
    assert results[0]["type"] == "tool_result"
    assert results[0]["tool_use_id"] == "t1"


@pytest.mark.asyncio
async def test_dispatcher_runs_independent_tools_in_parallel():
    events = []

    async def slow_tool(args, ctx):
        events.append("start")
        await asyncio.sleep(0.05)
        events.append("end")
        return ToolResult.ok(data="done")

    dispatcher = ToolDispatcher({"tool_a": slow_tool, "tool_b": slow_tool})
    ctx = make_ctx()
    tool_uses = [
        make_tool_use("t1", "tool_a", {}),
        make_tool_use("t2", "tool_b", {}),
    ]

    import time
    t0 = time.monotonic()
    results = await dispatcher.dispatch(tool_uses, ctx)
    elapsed = time.monotonic() - t0

    # If parallel: should finish in ~0.05s not ~0.10s
    assert elapsed < 0.09
    assert len(results) == 2


@pytest.mark.asyncio
async def test_dispatcher_returns_error_for_unknown_tool():
    dispatcher = ToolDispatcher({})
    ctx = make_ctx()
    tool_uses = [make_tool_use("t1", "nonexistent_tool", {})]
    results = await dispatcher.dispatch(tool_uses, ctx)

    assert len(results) == 1
    assert "UNKNOWN_TOOL" in results[0]["content"][0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_handles_tool_exception():
    async def broken_tool(args, ctx):
        raise RuntimeError("something went wrong")

    dispatcher = ToolDispatcher({"broken": broken_tool})
    ctx = make_ctx()
    tool_uses = [make_tool_use("t1", "broken", {})]
    results = await dispatcher.dispatch(tool_uses, ctx)

    assert len(results) == 1
    assert "something went wrong" in results[0]["content"][0]["text"]
