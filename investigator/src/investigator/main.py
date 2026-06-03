from __future__ import annotations
import os
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from investigator.context import RunContext, SessionState
from investigator.harness import AgentHarness
from investigator.agents.registry import AgentRegistry
from investigator.tools.mcp_client import MCPClient
from investigator.tools.bedrock_memory import BedrockMemoryTool
from investigator.observability.logging import configure_logging, get_logger
from investigator.observability.tracer import Tracer

load_dotenv()
configure_logging(dev=os.getenv("ENV", "dev") == "dev")
log = get_logger(__name__)

app = FastAPI(title="Code Investigator")

_sessions: dict[str, SessionState] = {}
_tracer = Tracer()

_BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")
_BEDROCK_MEMORY_ID = os.environ.get("BEDROCK_MEMORY_ID", "")
_MCP_SERVER_PATH = os.environ.get("MCP_SERVER_PATH", "../mcp-server-code-graph")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MAX_USD = float(os.environ.get("MAX_USD_PER_QUERY", "0.50"))
_MAX_TURNS = int(os.environ.get("MAX_TURNS", "25"))


class CreateSessionRequest(BaseModel):
    user_id: str
    repo_path: str = ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/sessions")
async def create_session(req: CreateSessionRequest):
    session_id = str(uuid.uuid4())
    mcp = MCPClient(server_path=_MCP_SERVER_PATH)
    BedrockMemoryTool(memory_id=_BEDROCK_MEMORY_ID, region=_AWS_REGION)

    state = SessionState(
        session_id=session_id,
        repo_path=Path(req.repo_path) if req.repo_path else Path("."),
        user_id=req.user_id,
        mcp_client=mcp,
    )
    _sessions[session_id] = state
    log.info("session_created", session_id=session_id, user_id=req.user_id)
    return {"session_id": session_id}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": state.session_id,
        "user_id": state.user_id,
        "repo_path": str(state.repo_path),
        "history_length": len(state.history),
    }


@app.post("/sessions/{session_id}/repo")
async def set_repo(session_id: str, body: dict):
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    state.repo_path = Path(body.get("repo_path", "."))
    return {"ok": True}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    state = _sessions.pop(session_id, None)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if state.mcp_client:
        try:
            await state.mcp_client.stop()
        except Exception as e:
            log.warning("mcp_client_stop_error", session_id=session_id, error=str(e))
    log.info("session_deleted", session_id=session_id)
    return {"ok": True}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    state = _sessions.get(session_id)
    if not state:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    memory = BedrockMemoryTool(memory_id=_BEDROCK_MEMORY_ID, region=_AWS_REGION)
    orchestrator_spec = AgentRegistry.get("orchestrator")

    try:
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type")

            if msg_type == "cancel":
                break

            if msg_type != "message":
                continue

            text = raw.get("text", "")
            if not text.strip():
                continue

            ctx = RunContext(
                agent_name="orchestrator",
                system_prompt=orchestrator_spec.system_prompt,
                tools=[],
                model=_BEDROCK_MODEL_ID,
                session_id=f"{state.user_id}:{state.repo_path}",
                max_usd=_MAX_USD,
                max_turns=_MAX_TURNS,
                websocket=websocket,
            )

            harness = AgentHarness(
                mcp_client=state.mcp_client,
                memory_tool=memory,
                tracer=_tracer,
            )

            try:
                if state.mcp_client and not getattr(state.mcp_client, "_proc", None):
                    await state.mcp_client.start()

                result = await harness.run(text, ctx)
                state.history.append({"role": "user", "content": text})
                state.history.append({"role": "assistant", "content": result.final_text})

                await websocket.send_json({
                    "type": "done",
                    "cost_usd": round(result.cost_usd, 4),
                    "turns": result.turns,
                })

            except Exception as e:
                log.error("harness_error", session_id=session_id, error=str(e))
                await websocket.send_json({"type": "error", "message": str(e)})

    except WebSocketDisconnect:
        log.info("ws_disconnected", session_id=session_id)
