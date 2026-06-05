from __future__ import annotations
import re
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from investigator.result import ToolResult


def _safe_session_id(value: str) -> str:
    """Sanitize to match AWS sessionId pattern: [a-zA-Z0-9][a-zA-Z0-9-_]*"""
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", value)
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    if not sanitized or not sanitized[0].isalnum():
        sanitized = "s-" + sanitized
    return sanitized[:128]


class BedrockMemoryTool:
    """
    Wraps Bedrock AgentCore Memory (data plane).

    Client: bedrock-agentcore
    Write:  create_event   — stores conversation findings as an event
    Read:   retrieve_memory_records — semantic search over stored events

    Namespace convention: /{user_id}/  (prefix covers all strategies)
    """

    def __init__(self, memory_id: str, region: str):
        self._memory_id = memory_id
        self._region = region
        self._client = boto3.client("bedrock-agentcore", region_name=region)

    # ------------------------------------------------------------------
    # Save: write a finding / session summary as a memory event
    # ------------------------------------------------------------------
    def save(self, user_id: str, repo_path: str, content: str) -> ToolResult:
        if not self._memory_id:
            return ToolResult.error("BEDROCK_MEMORY_ID not set", error_code="MEMORY_CONFIG_ERROR")
        t0 = time.monotonic()
        try:
            self._client.create_event(
                memoryId=self._memory_id,
                actorId=user_id,
                sessionId=_safe_session_id(repo_path or "default"),
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    {
                        "conversational": {
                            "content": {"text": content},
                            "role": "ASSISTANT",
                        }
                    }
                ],
            )
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.ok(data="saved", duration_ms=ms)
        except ClientError as e:
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.error(str(e), error_code="MEMORY_ERROR", duration_ms=ms)
        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.error(str(e), error_code="MEMORY_ERROR", duration_ms=ms)

    # ------------------------------------------------------------------
    # Search: semantic retrieval over stored memory records
    # ------------------------------------------------------------------
    def search(self, user_id: str, repo_path: str, query: str) -> ToolResult:
        if not self._memory_id:
            return ToolResult.error("BEDROCK_MEMORY_ID not set", error_code="MEMORY_CONFIG_ERROR")
        t0 = time.monotonic()
        try:
            resp = self._client.retrieve_memory_records(
                memoryId=self._memory_id,
                namespace=f"/{user_id}/",
                searchCriteria={
                    "searchQuery": query,
                    "topK": 5,
                },
            )
            items = resp.get("memoryRecordSummaries", [])
            texts = [i.get("content", {}).get("text", "") for i in items if i.get("content", {}).get("text")]
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.ok(data="\n---\n".join(texts) if texts else "No prior findings.", duration_ms=ms)
        except ClientError as e:
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.error(str(e), error_code="MEMORY_ERROR", duration_ms=ms)
        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            return ToolResult.error(str(e), error_code="MEMORY_ERROR", duration_ms=ms)

    # ------------------------------------------------------------------
    # Session persistence: store / reload session state via memory events
    # ------------------------------------------------------------------
    def save_session(self, session_id: str, user_id: str, repo_path: str) -> ToolResult:
        """Persist session metadata so it can survive server restarts."""
        if not self._memory_id:
            return ToolResult.ok(data="noop")
        try:
            self._client.create_event(
                memoryId=self._memory_id,
                actorId=user_id,
                sessionId=_safe_session_id(f"session-{session_id}"),
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    {
                        "conversational": {
                            "content": {"text": f"session_id={session_id} repo_path={repo_path}"},
                            "role": "ASSISTANT",
                        }
                    }
                ],
                metadata={
                    "session_id": {"stringValue": session_id},
                    "repo_path": {"stringValue": repo_path},
                    "user_id": {"stringValue": user_id},
                    "type": {"stringValue": "session_meta"},
                },
            )
            return ToolResult.ok(data="session persisted")
        except Exception as e:
            return ToolResult.error(str(e), error_code="MEMORY_ERROR")

    # ------------------------------------------------------------------
    # Tool schemas exposed to the orchestrator
    # ------------------------------------------------------------------
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
