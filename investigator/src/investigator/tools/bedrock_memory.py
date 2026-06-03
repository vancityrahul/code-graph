from __future__ import annotations
import time
import boto3
from investigator.result import ToolResult


class BedrockMemoryTool:
    """
    Wraps Bedrock Agent Core Memory Store.

    IMPORTANT: The exact boto3 method names below (retrieve_memory, create_memory_record)
    must be verified against the AWS Bedrock Agent Core docs before using with real AWS:
    https://docs.aws.amazon.com/bedrock/latest/userguide/agent-core-memory.html

    The boto3 service name may be 'bedrock-agentcore' rather than 'bedrock-agent-runtime'.
    Update _client_name, _save_method, and _search_method as needed.
    """

    _client_name = "bedrock-agent-runtime"
    _save_method = "create_memory_record"     # verify against AWS docs
    _search_method = "retrieve_memory"         # verify against AWS docs

    def __init__(self, memory_id: str, region: str):
        self._memory_id = memory_id
        self._client = boto3.client(self._client_name, region_name=region)

    def save(self, user_id: str, repo_path: str, content: str) -> ToolResult:
        t0 = time.monotonic()
        namespace = f"{user_id}::{repo_path}"
        try:
            method = getattr(self._client, self._save_method)
            method(
                memoryId=self._memory_id,
                namespace=namespace,
                content={"text": content},
            )
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.ok(data="saved", duration_ms=ms)
        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.error(str(e), error_code="MEMORY_ERROR", duration_ms=ms)

    def search(self, user_id: str, repo_path: str, query: str) -> ToolResult:
        t0 = time.monotonic()
        namespace = f"{user_id}::{repo_path}"
        try:
            method = getattr(self._client, self._search_method)
            resp = method(
                memoryId=self._memory_id,
                namespace=namespace,
                query={"text": query},
            )
            items = resp.get("memoryContents", [])
            texts = [i.get("content", {}).get("text", "") for i in items]
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.ok(data="\n---\n".join(texts), duration_ms=ms)
        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.error(str(e), error_code="MEMORY_ERROR", duration_ms=ms)

    def tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_save",
                "description": "Save investigation findings to persistent memory for this repo. Call at the end of a session with a concise summary of key findings.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Summary of findings to persist"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "Search past investigation findings for this repo. Call at the start of a session to check if this repo has been investigated before.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to look for in past findings"},
                    },
                    "required": ["query"],
                },
            },
        ]
