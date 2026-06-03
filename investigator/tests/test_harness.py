import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from investigator.harness import AgentHarness
from investigator.context import RunContext, StopReason
from investigator.result import ToolResult


def make_ctx(**kwargs):
    defaults = dict(
        agent_name="orchestrator",
        system_prompt="You are helpful.",
        tools=[],
        model="us.anthropic.claude-sonnet-test",
        session_id="s1",
        max_usd=1.0,
        max_turns=10,
    )
    defaults.update(kwargs)
    return RunContext(**defaults)


def make_mock_mcp():
    mcp = MagicMock()
    mcp.list_tools = AsyncMock(return_value=[])
    return mcp


def make_bedrock_response(text: str, stop_reason: str = "end_turn"):
    msg = MagicMock()
    msg.stop_reason = stop_reason
    block = MagicMock()
    block.type = "text"
    block.text = text
    msg.content = [block]
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    return msg


def make_tool_use_response(tool_id: str, tool_name: str, tool_args: dict):
    msg = MagicMock()
    msg.stop_reason = "tool_use"
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = tool_name
    block.input = tool_args
    msg.content = [block]
    msg.usage = MagicMock(input_tokens=200, output_tokens=80)
    return msg


@pytest.mark.asyncio
async def test_harness_returns_on_end_turn():
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=make_bedrock_response("Auth lives in pkg/identity/auth.py")
    )

    harness = AgentHarness(mcp_client=make_mock_mcp(), memory_tool=MagicMock())
    harness._bedrock = mock_client

    result = await harness.run("where is auth?", make_ctx())

    assert result.stop_reason == StopReason.END_TURN
    assert "Auth lives in" in result.final_text
    assert result.turns == 1


@pytest.mark.asyncio
async def test_harness_dispatches_tool_call():
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=[
        make_tool_use_response("t1", "read_file", {"path": "/foo.py"}),
        make_bedrock_response("Done reading."),
    ])

    harness = AgentHarness(mcp_client=make_mock_mcp(), memory_tool=MagicMock())
    harness._bedrock = mock_client

    result = await harness.run("read /foo.py", make_ctx())
    assert result.stop_reason == StopReason.END_TURN
    assert result.turns == 2


@pytest.mark.asyncio
async def test_harness_stops_on_turn_budget():
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=make_tool_use_response("t1", "read_file", {"path": "/x.py"})
    )

    harness = AgentHarness(mcp_client=make_mock_mcp(), memory_tool=MagicMock())
    harness._bedrock = mock_client

    ctx = make_ctx(max_turns=3)
    result = await harness.run("loop forever", ctx)

    assert result.stop_reason == StopReason.BUDGET_TURNS
    assert result.turns >= 3


@pytest.mark.asyncio
async def test_harness_stops_on_usd_budget():
    mock_client = MagicMock()
    resp = make_bedrock_response("answer")
    resp.usage = MagicMock(input_tokens=100_000, output_tokens=50_000)
    mock_client.messages.create = AsyncMock(return_value=resp)

    harness = AgentHarness(mcp_client=make_mock_mcp(), memory_tool=MagicMock())
    harness._bedrock = mock_client

    ctx = make_ctx(max_usd=0.001)
    result = await harness.run("expensive query", ctx)

    assert result.stop_reason in (StopReason.BUDGET_USD, StopReason.END_TURN)
