import pytest
from unittest.mock import AsyncMock, MagicMock
from investigator.tools.delegate import make_delegate_handler
from investigator.context import RunContext
from investigator.result import ToolResult


def make_ctx():
    return RunContext(
        agent_name="orchestrator",
        system_prompt="",
        tools=[],
        session_id="s1",
        max_usd=0.50,
        cost_usd=0.10,
    )


@pytest.mark.asyncio
async def test_delegate_calls_subagent_harness():
    mock_result = MagicMock()
    mock_result.final_text = "auth is in pkg/identity"
    mock_result.cost_usd = 0.02

    mock_harness_class = MagicMock()
    captured = {}

    async def capture_run(task, sub_ctx):
        captured["task"] = task
        return mock_result

    mock_harness_class.return_value.run = capture_run

    handler = make_delegate_handler(
        mcp_client=MagicMock(),
        memory_tool=MagicMock(),
        harness_factory=mock_harness_class,
    )
    ctx = make_ctx()
    result = await handler(
        {"agent": "code_explorer", "task": "find auth", "context": "repo is fastapi"},
        ctx,
    )

    assert result.ok is True
    assert "auth is in pkg/identity" in result.data
    assert "repo is fastapi" in captured["task"]


@pytest.mark.asyncio
async def test_delegate_rejects_unknown_agent():
    handler = make_delegate_handler(
        mcp_client=MagicMock(),
        memory_tool=MagicMock(),
    )
    ctx = make_ctx()
    result = await handler({"agent": "hacker_agent", "task": "do bad things"}, ctx)
    assert result.ok is False
    assert result.error_code == "UNKNOWN_AGENT"


@pytest.mark.asyncio
async def test_delegate_budget_slice():
    mock_result = MagicMock()
    mock_result.final_text = "done"
    mock_result.cost_usd = 0.01

    mock_harness_class = MagicMock()
    captured_ctx = {}

    async def capture_run(task, sub_ctx):
        captured_ctx["ctx"] = sub_ctx
        return mock_result

    mock_harness_class.return_value.run = capture_run

    handler = make_delegate_handler(
        mcp_client=MagicMock(),
        memory_tool=MagicMock(),
        harness_factory=mock_harness_class,
    )
    ctx = make_ctx()  # max_usd=0.50, cost_usd=0.10 → remaining=0.40
    await handler({"agent": "test_analyst", "task": "check coverage"}, ctx)

    sub_ctx = captured_ctx["ctx"]
    # Sub-agent gets ⅓ of remaining budget: 0.40 / 3 ≈ 0.133
    assert sub_ctx.max_usd < 0.20
    assert sub_ctx.agent_name == "test_analyst"
