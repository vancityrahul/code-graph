"""MCP stdio server for the Code Graph query tools."""
from __future__ import annotations

import argparse
import asyncio
import json

import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from . import query as Q

_DEFAULT_DB = ".investigator/graph.kuzu"

_TOOLS = [
    types.Tool(
        name="index_repo",
        description="Index or re-index a code repository. Call this first.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "db_path": {"type": "string"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
    ),
    types.Tool(
        name="index_status",
        description="Return indexing metadata: last indexed time, file count, schema version.",
        inputSchema={
            "type": "object",
            "properties": {"db_path": {"type": "string"}},
        },
    ),
    types.Tool(
        name="find_symbol",
        description="Find classes, functions, methods, or tests by name.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["class", "function", "method", "test"]},
                "fuzzy": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 20},
                "db_path": {"type": "string"},
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="get_file_summary",
        description="List classes, functions, and methods in a file without reading it.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "db_path": {"type": "string"},
            },
            "required": ["path"],
        },
    ),
    types.Tool(
        name="get_callers",
        description="Find functions/methods that call the given qualified name.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "db_path": {"type": "string"},
            },
            "required": ["qualified_name"],
        },
    ),
    types.Tool(
        name="get_callees",
        description="Find functions/methods called by the given qualified name.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "db_path": {"type": "string"},
            },
            "required": ["qualified_name"],
        },
    ),
    types.Tool(
        name="find_path",
        description="Find the shortest call path between two symbols.",
        inputSchema={
            "type": "object",
            "properties": {
                "from_symbol": {"type": "string"},
                "to_symbol": {"type": "string"},
                "max_depth": {"type": "integer", "default": 5},
                "db_path": {"type": "string"},
            },
            "required": ["from_symbol", "to_symbol"],
        },
    ),
    types.Tool(
        name="find_references",
        description="Find all callers and inheritors of a given symbol.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "db_path": {"type": "string"},
            },
            "required": ["qualified_name"],
        },
    ),
    types.Tool(
        name="get_imports",
        description="Return the import list for a given file path.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_or_module": {"type": "string"},
                "db_path": {"type": "string"},
            },
            "required": ["file_or_module"],
        },
    ),
    types.Tool(
        name="get_test_coverage",
        description="Find tests that cover a given function or method.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "db_path": {"type": "string"},
            },
            "required": ["qualified_name"],
        },
    ),
    types.Tool(
        name="query_graph",
        description="[GATED] Run a raw Cypher query. Only works when enabled=true.",
        inputSchema={
            "type": "object",
            "properties": {
                "cypher": {"type": "string"},
                "enabled": {"type": "boolean", "default": False},
                "db_path": {"type": "string"},
            },
            "required": ["cypher"],
        },
    ),
]


def build_server(default_db: str) -> Server:
    server = Server("mcp-server-code-graph")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return _TOOLS

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict | None
    ) -> list[types.TextContent]:
        args = arguments or {}
        db = args.get("db_path") or default_db

        try:
            if name == "index_repo":
                text = Q.index_repo(args["path"], db_path=db, force=args.get("force", False))
            elif name == "index_status":
                text = Q.index_status(db_path=db)
            elif name == "find_symbol":
                text = Q.find_symbol(
                    args["name"], db_path=db,
                    kind=args.get("kind"),
                    fuzzy=args.get("fuzzy", False),
                    limit=args.get("limit", 20),
                )
            elif name == "get_file_summary":
                text = Q.get_file_summary(args["path"], db_path=db)
            elif name == "get_callers":
                text = Q.get_callers(args["qualified_name"], db_path=db, depth=args.get("depth", 1))
            elif name == "get_callees":
                text = Q.get_callees(args["qualified_name"], db_path=db, depth=args.get("depth", 1))
            elif name == "find_path":
                text = Q.find_path(
                    args["from_symbol"], args["to_symbol"],
                    db_path=db, max_depth=args.get("max_depth", 5),
                )
            elif name == "find_references":
                text = Q.find_references(args["qualified_name"], db_path=db)
            elif name == "get_imports":
                text = Q.get_imports(args["file_or_module"], db_path=db)
            elif name == "get_test_coverage":
                text = Q.get_test_coverage(args["qualified_name"], db_path=db)
            elif name == "query_graph":
                text = Q.query_graph(
                    args["cypher"], db_path=db,
                    enabled=args.get("enabled", False),
                )
            else:
                from .result import ToolResult
                text = ToolResult.error(f"Unknown tool: {name}", code="UNKNOWN_TOOL").to_json()
        except Exception as exc:
            from .result import ToolResult
            text = ToolResult.error(str(exc), code="INTERNAL_ERROR").to_json()

        return [types.TextContent(type="text", text=text)]

    return server


async def _run(default_db: str) -> None:
    server = build_server(default_db)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="mcp-server-code-graph",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Code Graph MCP stdio server")
    parser.add_argument("--db-path", default=_DEFAULT_DB)
    args = parser.parse_args()
    asyncio.run(_run(default_db=args.db_path))


if __name__ == "__main__":
    main()
