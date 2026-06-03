from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path
from investigator.result import ToolResult


class MCPClient:
    def __init__(self, server_path: str):
        self._server_path = server_path
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "uv", "run", "code-graph-mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._server_path,
        )

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()

    async def list_tools(self) -> list[dict]:
        resp = await self._rpc("tools/list", {})
        return resp.get("tools", [])

    async def call_tool(self, name: str, args: dict) -> ToolResult:
        t0 = time.monotonic()
        try:
            resp = await self._rpc("tools/call", {"name": name, "arguments": args})
        except MCPError as e:
            return ToolResult.error(str(e), error_code="MCP_ERROR")

        content = resp.get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), "")
        ms = int((time.monotonic() - t0) * 1000)

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "ok" in parsed:
                if parsed["ok"]:
                    return ToolResult.ok(
                        data=parsed.get("data", text),
                        duration_ms=ms,
                        truncated=parsed.get("meta", {}).get("truncated", False),
                    )
                return ToolResult.error(
                    parsed.get("error", "unknown error"),
                    error_code=parsed.get("error_code", "MCP_ERROR"),
                    duration_ms=ms,
                )
        except (json.JSONDecodeError, TypeError):
            pass

        return ToolResult.ok(data=text, duration_ms=ms)

    async def _rpc(self, method: str, params: dict) -> dict:
        req_id = self._next_id
        self._next_id += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        self._proc.stdin.write((msg + "\n").encode())
        await self._proc.stdin.drain()

        line = await self._proc.stdout.readline()
        resp = json.loads(line.decode().strip())

        if "error" in resp:
            raise MCPError(resp["error"].get("message", "rpc error"))
        return resp.get("result", {})


class MCPError(Exception):
    pass
