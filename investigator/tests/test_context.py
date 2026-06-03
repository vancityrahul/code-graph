import pytest
from pathlib import Path
from investigator.context import RunContext, SessionState, RunResult, StopReason


def test_run_context_defaults():
    ctx = RunContext(agent_name="orchestrator", system_prompt="You are...", tools=[])
    assert ctx.max_turns == 25
    assert ctx.max_usd == 0.50
    assert ctx.cost_usd == 0.0
    assert ctx.turn_count == 0


def test_run_context_budget_exceeded():
    ctx = RunContext(agent_name="orchestrator", system_prompt="", tools=[], max_usd=0.01)
    ctx.cost_usd = 0.02
    assert ctx.is_over_budget() is True


def test_run_context_turn_exceeded():
    ctx = RunContext(agent_name="orchestrator", system_prompt="", tools=[], max_turns=3)
    ctx.turn_count = 3
    assert ctx.is_over_turns() is True


def test_run_context_add_cost():
    ctx = RunContext(agent_name="orchestrator", system_prompt="", tools=[])
    ctx.add_cost(input_tokens=1000, output_tokens=200, model="claude-sonnet")
    assert ctx.cost_usd > 0.0


def test_run_result_from_text():
    result = RunResult(
        final_text="Auth is in pkg/identity/auth.py",
        stop_reason=StopReason.END_TURN,
        cost_usd=0.02,
        turns=3,
    )
    assert result.final_text == "Auth is in pkg/identity/auth.py"
    assert result.stop_reason == StopReason.END_TURN


def test_session_state_construction():
    state = SessionState(
        session_id="abc123",
        repo_path=Path("/tmp/myrepo"),
        user_id="user1",
    )
    assert state.session_id == "abc123"
    assert state.history == []
