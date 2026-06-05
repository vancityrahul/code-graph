"""Agent harness built on the Claude Agent SDK.

The agent loop, tool dispatch, subagent delegation, and session state are all
handled by `claude_agent_sdk.query()`. This module wires our domain into it:

  - orchestrator + specialists  -> ClaudeAgentOptions.agents (AgentDefinition)
  - code-graph MCP server       -> stdio MCP server (its own venv exe)
  - Bedrock memory tools        -> in-process SDK MCP server (@tool)
  - built-in Read/Grep/Glob     -> replace the old first-party file tools
  - Langfuse tracing            -> driven from the message stream
  - USD + turn budgets          -> ClaudeAgentOptions.max_budget_usd / max_turns

Bedrock auth comes from CLAUDE_CODE_USE_BEDROCK=1 + AWS creds in the environment.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AgentDefinition,
    create_sdk_mcp_server,
    tool,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    StreamEvent,
    UserMessage,
)

from investigator.context import RunContext, RunResult, StopReason
from investigator.agents.registry import AgentRegistry, AGENT_NAMES
from investigator.tools.bedrock_memory import BedrockMemoryTool
from investigator.observability.tracer import Tracer
from investigator.observability.logging import get_logger

log = get_logger(__name__)


# ── tool-name mapping: internal spec names -> Agent SDK tool names ────────────
_BUILTIN_MAP = {
    "read_file": "Read",
    "grep": "Grep",
    "list_dir": "Glob",
    "memory_save": "mcp__memory__memory_save",
    "memory_search": "mcp__memory__memory_search",
    "delegate_to_subagent": "Task",
}

_CODEGRAPH_TOOLS = {
    "index_repo", "index_status", "find_symbol", "get_callers", "get_callees",
    "find_references", "get_imports", "get_file_summary", "find_path",
    "get_test_coverage", "query_graph",
}


def _map_tool(name: str) -> str:
    if name in _BUILTIN_MAP:
        return _BUILTIN_MAP[name]
    if name in _CODEGRAPH_TOOLS:
        return f"mcp__codegraph__{name}"
    return name


def _codegraph_exe(server_path: str) -> str:
    """Path to the code-graph MCP server's installed entry-point exe."""
    base = Path(server_path)
    win = base / ".venv" / "Scripts" / "code-graph-mcp.exe"
    nix = base / ".venv" / "bin" / "code-graph-mcp"
    if win.exists():
        return str(win)
    if nix.exists():
        return str(nix)
    # Fall back to uv run --directory if the exe isn't built yet
    return ""


class AgentHarness:
    def __init__(
        self,
        memory_tool: BedrockMemoryTool,
        tracer: Tracer | None = None,
        mcp_server_path: str = "../mcp-server-code-graph",
    ):
        self._memory = memory_tool
        self._tracer = tracer or Tracer()
        self._mcp_server_path = str(Path(mcp_server_path).resolve())

    # ── in-process MCP server exposing Bedrock memory as SDK tools ────────────
    def _build_memory_server(self, user_id: str, repo_path: str):
        memory = self._memory

        @tool("memory_save", "Save investigation findings to persistent memory for this repo. Call at the end with a concise summary.", {"content": str})
        async def memory_save(args: dict) -> dict:
            r = memory.save(user_id=user_id, repo_path=repo_path, content=args.get("content", ""))
            text = r.data if r.ok else f"error: {r.error}"
            return {"content": [{"type": "text", "text": str(text)}]}

        @tool("memory_search", "Search past investigation findings for this repo. Call at the start to check if it was investigated before.", {"query": str})
        async def memory_search(args: dict) -> dict:
            r = memory.search(user_id=user_id, repo_path=repo_path, query=args.get("query", ""))
            text = r.data if r.ok else f"error: {r.error}"
            return {"content": [{"type": "text", "text": str(text)}]}

        return create_sdk_mcp_server("memory", "1.0.0", tools=[memory_save, memory_search])

    # ── subagents from the registry (everything except the orchestrator) ──────
    def _build_agents(self) -> dict[str, AgentDefinition]:
        agents: dict[str, AgentDefinition] = {}
        for name in AGENT_NAMES:
            if name == "orchestrator":
                continue
            spec = AgentRegistry.get(name)
            if not spec:
                continue
            agents[name] = AgentDefinition(
                description=f"{name.replace('_', ' ')} specialist",
                prompt=spec.system_prompt,
                tools=[_map_tool(t) for t in spec.tool_names],
                model="inherit",
                maxTurns=spec.max_turns,
            )
        return agents

    def _codegraph_mcp_config(self) -> dict:
        exe = _codegraph_exe(self._mcp_server_path)
        if exe:
            return {"type": "stdio", "command": exe, "args": []}
        # Fallback: launch via uv from the server directory
        return {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "--directory", self._mcp_server_path, "code-graph-mcp"],
        }

    async def run(self, query_text: str, ctx: RunContext) -> RunResult:
        spec = AgentRegistry.get("orchestrator")
        user_id, _, repo_path = ctx.session_id.partition(":")

        memory_server = self._build_memory_server(user_id, repo_path)
        codegraph_server = self._codegraph_mcp_config()

        allowed = [_map_tool(t) for t in (spec.tool_names if spec else [])]

        opts = ClaudeAgentOptions(
            model=ctx.model or os.environ.get("BEDROCK_MODEL_ID", ""),
            system_prompt=spec.system_prompt if spec else "",
            cwd=repo_path or ".",
            setting_sources=[],                 # do NOT inherit local .claude config
            permission_mode="bypassPermissions",  # read-only investigation; auto-approve
            max_turns=ctx.max_turns,
            max_budget_usd=ctx.max_usd,
            mcp_servers={"codegraph": codegraph_server, "memory": memory_server},
            allowed_tools=allowed,
            agents=self._build_agents(),
            include_partial_messages=True,
            resume=getattr(ctx, "resume_id", None) or None,
        )

        ws = ctx.websocket
        final_text = ""
        cost_usd = 0.0
        turns = 0
        session_id = ""
        tools_called: list[str] = []
        stop = StopReason.END_TURN

        async with self._tracer.trace("orchestrator.run", input=query_text, session_id=ctx.session_id) as trace:
            open_tool_spans: dict[str, Any] = {}
            async for msg in query(prompt=query_text, options=opts):
                # ── live token streaming ──────────────────────────────────────
                if isinstance(msg, StreamEvent):
                    delta = _stream_text_delta(msg)
                    if delta and ws:
                        await ws.send_json({"type": "token", "text": delta})
                    continue

                # ── assistant turn: capture text + tool starts ────────────────
                if isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, TextBlock) and b.text:
                            final_text = b.text  # last assistant text wins
                        elif isinstance(b, ToolUseBlock):
                            tools_called.append(b.name)
                            if ws:
                                await ws.send_json({"type": "tool_start", "tool": b.name, "args": b.input})
                            # open a Langfuse tool span keyed by tool_use id
                            cm = trace.span(b.name, as_type="tool", input=b.input)
                            span = await cm.__aenter__()
                            open_tool_spans[b.id] = (cm, span)
                    continue

                # ── tool results arrive as a UserMessage; close spans ─────────
                if isinstance(msg, UserMessage):
                    for b in getattr(msg, "content", []) or []:
                        if isinstance(b, ToolResultBlock):
                            entry = open_tool_spans.pop(b.tool_use_id, None)
                            if entry:
                                cm, span = entry
                                span.finish(output=_result_text(b))
                                await cm.__aexit__(None, None, None)
                            if ws:
                                await ws.send_json({"type": "tool_end", "tool": "", "ok": not getattr(b, "is_error", False)})
                    continue

                # ── final result ──────────────────────────────────────────────
                if isinstance(msg, ResultMessage):
                    if msg.result:
                        final_text = msg.result
                    cost_usd = msg.total_cost_usd or 0.0
                    turns = msg.num_turns or 0
                    session_id = msg.session_id or ""
                    if msg.is_error:
                        stop = StopReason.BUDGET_USD if "budget" in str(msg.subtype).lower() else StopReason.BUDGET_TURNS

            # close any tool spans left open (defensive)
            for cm, span in open_tool_spans.values():
                try:
                    await cm.__aexit__(None, None, None)
                except Exception:
                    pass

            trace.finish(
                output=final_text,
                metadata={
                    "model": ctx.model,
                    "turns": turns,
                    "total_cost_usd": round(cost_usd, 6),
                    "tools_called": tools_called,
                    "sdk_session_id": session_id,
                },
            )

        log.info("run_complete", turns=turns, cost_usd=round(cost_usd, 6), session=session_id)
        result = RunResult(
            final_text=final_text,
            stop_reason=stop,
            cost_usd=cost_usd,
            turns=turns,
        )
        result.sdk_session_id = session_id  # type: ignore[attr-defined]
        return result


# ── helpers ───────────────────────────────────────────────────────────────────
def _stream_text_delta(event: StreamEvent) -> str:
    """Pull a text delta out of a partial-message StreamEvent, if present."""
    data = getattr(event, "event", None) or getattr(event, "data", None)
    if isinstance(data, dict):
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")
    return ""


def _result_text(block: ToolResultBlock) -> str:
    content = getattr(block, "content", None)
    if isinstance(content, str):
        return content[:1000]
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text", ""))[:1000]
    return ""
