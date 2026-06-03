from __future__ import annotations
from typing import Any, Callable

from investigator.agents.registry import AgentRegistry, AGENT_NAMES as _ALL_AGENT_NAMES
from investigator.context import RunContext
from investigator.result import ToolResult

# Sub-agents that may be delegated to — derived from registry, excluding orchestrator.
_DELEGATABLE_AGENTS = set(_ALL_AGENT_NAMES) - {"orchestrator"}

DELEGATE_SCHEMA = {
    "name": "delegate_to_subagent",
    "description": (
        "Delegate a focused sub-task to a specialist agent. "
        "Returns only the sub-agent's final answer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": sorted(_DELEGATABLE_AGENTS),
                "description": "Which specialist to invoke",
            },
            "task": {"type": "string", "description": "Specific, scoped question or task"},
            "context": {"type": "string", "description": "Background the sub-agent needs"},
            "max_turns": {"type": "integer", "default": 15},
        },
        "required": ["agent", "task"],
    },
}


def make_delegate_handler(
    mcp_client: Any,
    memory_tool: Any,
    harness_factory: Callable | None = None,
):
    """Return an async handler for the delegate_to_subagent tool.

    harness_factory: optional override for the AgentHarness class — used in
    tests to inject a mock without triggering the circular import from harness.py.
    In production, harness_factory is None and AgentHarness is imported lazily
    inside the handler to avoid the circular import chain:
      delegate → harness → dispatcher → tools → delegate
    """

    async def handler(args: dict, ctx: RunContext) -> ToolResult:
        agent_name = args.get("agent", "")
        task = args.get("task", "")
        extra_context = args.get("context", "")
        try:
            max_turns = int(args.get("max_turns", 15))
        except (ValueError, TypeError):
            return ToolResult.error("max_turns must be an integer", error_code="INVALID_ARGUMENT")

        # Validate agent name.
        if agent_name not in _DELEGATABLE_AGENTS:
            return ToolResult.error(
                f"Unknown agent '{agent_name}'. Valid: {sorted(_DELEGATABLE_AGENTS)}",
                error_code="UNKNOWN_AGENT",
            )

        spec = AgentRegistry.get(agent_name)
        if spec is None:
            return ToolResult.error(
                f"Agent '{agent_name}' has no spec in registry.",
                error_code="UNKNOWN_AGENT",
            )

        # Resolve the harness class — lazy import avoids circular dep at module load.
        if harness_factory is not None:
            HarnessCls = harness_factory
        else:
            from investigator.harness import AgentHarness  # noqa: PLC0415
            HarnessCls = AgentHarness

        # Build sub-context (budget sliced to ⅓ of remaining).
        sub_ctx = ctx.child_context(
            agent_name=agent_name,
            system_prompt=spec.system_prompt,
            tools=[],       # harness resolves tool list from spec
            max_turns=max_turns,
        )

        # Build the task string passed to the harness.
        full_task = task
        if extra_context:
            full_task = f"{task}\n\nContext:\n{extra_context}"

        harness = HarnessCls(
            agent_spec=spec,
            mcp_client=mcp_client,
            memory_tool=memory_tool,
        )
        sub_result = await harness.run(full_task, sub_ctx)

        return ToolResult.ok(sub_result.final_text)

    return handler
