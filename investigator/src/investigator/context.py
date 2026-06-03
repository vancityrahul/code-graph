from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# Approximate Bedrock pricing for Claude Sonnet (USD per 1M tokens).
# Update these when AWS publishes new pricing.
_INPUT_PRICE_PER_MTK = 3.0
_OUTPUT_PRICE_PER_MTK = 15.0


class StopReason(str, Enum):
    END_TURN = "end_turn"
    BUDGET_USD = "budget_usd"
    BUDGET_TURNS = "budget_turns"
    REPETITION = "repetition"
    CANCELLED = "cancelled"


@dataclass
class RunContext:
    agent_name: str
    system_prompt: str
    tools: list[dict]
    max_turns: int = 25
    max_usd: float = 0.50
    model: str = ""           # set from BEDROCK_MODEL_ID env var at harness init
    session_id: str = ""
    parent_correlation_id: str | None = None
    cost_usd: float = 0.0
    turn_count: int = 0
    websocket: Any | None = None   # only set on orchestrator, None for sub-agents

    def is_over_budget(self) -> bool:
        return self.cost_usd >= self.max_usd

    def is_over_turns(self) -> bool:
        return self.turn_count >= self.max_turns

    def add_cost(self, input_tokens: int, output_tokens: int, model: str) -> None:
        input_cost = (input_tokens / 1_000_000) * _INPUT_PRICE_PER_MTK
        output_cost = (output_tokens / 1_000_000) * _OUTPUT_PRICE_PER_MTK
        self.cost_usd += input_cost + output_cost

    def child_context(self, agent_name: str, system_prompt: str, tools: list[dict], max_turns: int = 15) -> "RunContext":
        remaining_budget = max(0.0, self.max_usd - self.cost_usd)
        return RunContext(
            agent_name=agent_name,
            system_prompt=system_prompt,
            tools=tools,
            max_turns=max_turns,
            max_usd=remaining_budget / 3,
            model=self.model,
            session_id=self.session_id,
            parent_correlation_id=self.session_id,
            websocket=None,
        )


@dataclass
class RunResult:
    final_text: str
    stop_reason: StopReason
    cost_usd: float
    turns: int

    @classmethod
    def budget_exceeded(cls, cost_usd: float, turns: int) -> "RunResult":
        return cls(
            final_text="",
            stop_reason=StopReason.BUDGET_USD,
            cost_usd=cost_usd,
            turns=turns,
        )


@dataclass
class SessionState:
    session_id: str
    repo_path: Path
    user_id: str
    mcp_client: Any | None = None
    langfuse_trace_id: str = ""
    history: list[dict] = field(default_factory=list)
