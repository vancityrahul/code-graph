from __future__ import annotations
import os
import anthropic
from investigator.context import RunContext, RunResult, StopReason
from investigator.dispatcher import ToolDispatcher
from investigator.result import ToolResult
from investigator.agents.registry import AgentRegistry, AgentSpec
from investigator.tools.first_party import read_file, grep, list_dir
from investigator.tools.mcp_client import MCPClient
from investigator.tools.bedrock_memory import BedrockMemoryTool
from investigator.observability.tracer import Tracer
from investigator.observability.logging import get_logger
from pathlib import Path

log = get_logger(__name__)


def _wrap_first_party(fn):
    async def handler(args: dict, ctx: RunContext) -> ToolResult:
        path_arg = args.get("path")
        if path_arg is not None:
            other = {k: v for k, v in args.items() if k != "path"}
            return fn(Path(path_arg), **other)
        return fn(**args)
    return handler


class AgentHarness:
    def __init__(
        self,
        mcp_client: MCPClient,
        memory_tool: BedrockMemoryTool,
        tracer: Tracer | None = None,
        agent_spec: AgentSpec | None = None,
    ):
        self._mcp = mcp_client
        self._memory = memory_tool
        self._tracer = tracer or Tracer()
        self._agent_spec = agent_spec  # optional override; normally resolved from ctx
        self._bedrock = anthropic.AsyncAnthropicBedrock(
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            aws_access_key=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        )

    def _build_dispatcher(self, ctx: RunContext) -> ToolDispatcher:
        from investigator.tools.delegate import make_delegate_handler

        spec = self._agent_spec or AgentRegistry.get(ctx.agent_name)
        allowed = set(spec.tool_names if spec else [])

        handlers: dict = {}

        if "read_file" in allowed:
            handlers["read_file"] = _wrap_first_party(read_file)
        if "grep" in allowed:
            handlers["grep"] = _wrap_first_party(grep)
        if "list_dir" in allowed:
            handlers["list_dir"] = _wrap_first_party(list_dir)

        if "memory_save" in allowed:
            handlers["memory_save"] = self._make_memory_save_handler()
        if "memory_search" in allowed:
            handlers["memory_search"] = self._make_memory_search_handler()
        if "delegate_to_subagent" in allowed:
            handlers["delegate_to_subagent"] = make_delegate_handler(self._mcp, self._memory)

        # MCP tools are dispatched dynamically via the mcp_client
        for tool_name in allowed:
            if tool_name not in handlers:
                handlers[tool_name] = self._make_mcp_handler(tool_name)

        return ToolDispatcher(handlers)

    def _make_mcp_handler(self, tool_name: str):
        async def handler(args: dict, ctx: RunContext) -> ToolResult:
            return await self._mcp.call_tool(tool_name, args)
        return handler

    def _make_memory_save_handler(self):
        async def handler(args: dict, ctx: RunContext) -> ToolResult:
            parts = ctx.session_id.split(":", 1)
            user_id = parts[0]
            repo_path = parts[1] if len(parts) > 1 else ""
            return self._memory.save(user_id=user_id, repo_path=repo_path, content=args.get("content", ""))
        return handler

    def _make_memory_search_handler(self):
        async def handler(args: dict, ctx: RunContext) -> ToolResult:
            parts = ctx.session_id.split(":", 1)
            user_id = parts[0]
            repo_path = parts[1] if len(parts) > 1 else ""
            return self._memory.search(user_id=user_id, repo_path=repo_path, query=args.get("query", ""))
        return handler

    def _build_tool_schemas(self, ctx: RunContext, mcp_schemas: list[dict]) -> list[dict]:
        from investigator.tools.delegate import DELEGATE_SCHEMA

        spec = self._agent_spec or AgentRegistry.get(ctx.agent_name)
        if not spec:
            return []

        schemas = []

        first_party_schemas = {
            "read_file": {
                "name": "read_file",
                "description": "Read a file with line numbers. Capped at 2000 lines.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
            "grep": {
                "name": "grep",
                "description": "Search for a pattern in the repo. Returns file:line:content matches, capped at 100.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "file_glob": {"type": "string"},
                    },
                    "required": ["pattern", "path"],
                },
            },
            "list_dir": {
                "name": "list_dir",
                "description": "List directory contents as a tree. Capped at 200 entries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_depth": {"type": "integer", "default": 3},
                    },
                    "required": ["path"],
                },
            },
        }

        memory_schemas = {s["name"]: s for s in self._memory.tool_schemas()}

        # MCP schemas, filtered to this agent's allowed set
        mcp_by_name = {
            s["name"]: {
                "name": s["name"],
                "description": s.get("description", ""),
                "input_schema": s.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for s in mcp_schemas
        }

        for tool_name in spec.tool_names:
            if tool_name in first_party_schemas:
                schemas.append(first_party_schemas[tool_name])
            elif tool_name in memory_schemas:
                schemas.append(memory_schemas[tool_name])
            elif tool_name == "delegate_to_subagent":
                schemas.append(DELEGATE_SCHEMA)
            elif tool_name in mcp_by_name:
                schemas.append(mcp_by_name[tool_name])

        return schemas

    async def run(self, query: str, ctx: RunContext) -> RunResult:
        dispatcher = self._build_dispatcher(ctx)

        mcp_tool_schemas: list[dict] = []
        if self._mcp:
            try:
                mcp_tool_schemas = await self._mcp.list_tools()
            except Exception as e:
                log.warning("mcp_list_tools_failed", error=str(e))

        tool_schemas = self._build_tool_schemas(ctx, mcp_tool_schemas)

        messages: list[dict] = [{"role": "user", "content": query}]

        with self._tracer.trace(f"{ctx.agent_name}.run", session_id=ctx.session_id) as trace:
            for turn in range(ctx.max_turns):
                ctx.turn_count = turn + 1

                if ctx.is_over_budget():
                    log.warning("budget_exceeded", agent=ctx.agent_name, cost=ctx.cost_usd)
                    return RunResult.budget_exceeded(ctx.cost_usd, turn)

                with trace.span("turn", turn=turn, agent=ctx.agent_name) as span:
                    call_kwargs: dict = dict(
                        model=ctx.model or os.environ.get("BEDROCK_MODEL_ID", ""),
                        max_tokens=8096,
                        system=ctx.system_prompt,
                        messages=messages,
                    )
                    if tool_schemas:
                        call_kwargs["tools"] = tool_schemas

                    should_stream = ctx.websocket is not None and ctx.agent_name == "orchestrator"

                    if should_stream:
                        resp = await self._stream_turn(call_kwargs, ctx)
                    else:
                        resp = await self._bedrock.messages.create(**call_kwargs)

                    ctx.add_cost(resp.usage.input_tokens, resp.usage.output_tokens, ctx.model)
                    span.set("input_tokens", resp.usage.input_tokens)
                    span.set("output_tokens", resp.usage.output_tokens)
                    span.set("cost_usd", ctx.cost_usd)
                    span.set("stop_reason", resp.stop_reason)
                    log.info("turn", agent=ctx.agent_name, turn=turn, stop=resp.stop_reason)

                    # Serialize to plain dicts — Bedrock adds extra fields (e.g. `caller`)
                    # on response blocks that it rejects if echoed back verbatim.
                    serialized = []
                    for b in resp.content:
                        t = getattr(b, "type", "")
                        if t == "text":
                            serialized.append({"type": "text", "text": b.text})
                        elif t == "tool_use":
                            serialized.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                    messages.append({"role": "assistant", "content": serialized})

                    if resp.stop_reason == "end_turn":
                        text = next(
                            (b.text for b in resp.content if getattr(b, "type", "") == "text"),
                            "",
                        )
                        return RunResult(
                            final_text=text,
                            stop_reason=StopReason.END_TURN,
                            cost_usd=ctx.cost_usd,
                            turns=turn + 1,
                        )

                    tool_uses = [
                        {"type": b.type, "id": b.id, "name": b.name, "input": b.input}
                        for b in resp.content
                        if getattr(b, "type", "") == "tool_use"
                    ]

                    if ctx.websocket:
                        for tu in tool_uses:
                            await ctx.websocket.send_json({"type": "tool_start", "tool": tu["name"], "args": tu["input"]})

                    with trace.span("tools") as tool_span:
                        tool_results = await dispatcher.dispatch(tool_uses, ctx)
                        tool_span.set("count", len(tool_uses))

                    if ctx.websocket:
                        for tu in tool_uses:
                            await ctx.websocket.send_json({"type": "tool_end", "tool": tu["name"], "ok": True})

                    messages.append({"role": "user", "content": tool_results})

        return RunResult(
            final_text="",
            stop_reason=StopReason.BUDGET_TURNS,
            cost_usd=ctx.cost_usd,
            turns=ctx.max_turns,
        )

    async def _stream_turn(self, call_kwargs: dict, ctx: RunContext) -> anthropic.types.Message:
        async with self._bedrock.messages.stream(**call_kwargs) as stream:
            async for text_chunk in stream.text_stream:
                if ctx.websocket:
                    await ctx.websocket.send_json({"type": "token", "text": text_chunk})
        return await stream.get_final_message()
