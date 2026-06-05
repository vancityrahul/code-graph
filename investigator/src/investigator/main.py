from __future__ import annotations
import os
import re
import subprocess
import uuid
import zipfile
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from investigator.context import RunContext, SessionState
from investigator.harness import AgentHarness
from investigator.agents.registry import AgentRegistry
from investigator.tools.bedrock_memory import BedrockMemoryTool
from investigator.observability.logging import configure_logging, get_logger
from investigator.observability.tracer import Tracer

load_dotenv()
# Route the Agent SDK through Amazon Bedrock (uses AWS creds from the environment)
os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "1")
configure_logging(dev=os.getenv("ENV", "dev") == "dev")
log = get_logger(__name__)

app = FastAPI(title="Code Investigator")

_sessions: dict[str, SessionState] = {}
_tracer = Tracer()

_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")
_BEDROCK_MEMORY_ID = os.environ.get("BEDROCK_MEMORY_ID", "")
_MCP_SERVER_PATH = os.environ.get("MCP_SERVER_PATH", "../mcp-server-code-graph")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MAX_USD = float(os.environ.get("MAX_USD_PER_QUERY", "0.50"))
_MAX_TURNS = int(os.environ.get("MAX_TURNS", "25"))
_S3_REPO_BUCKET = os.environ.get("S3_REPO_BUCKET", "")

_REPOS_CACHE_DIR = Path(tempfile.gettempdir()) / "code-graph-repos"


def _repo_name_from_url(url: str) -> str:
    name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1])
    return re.sub(r"[^a-zA-Z0-9_\-]", "-", name)


def _s3_prefix(repo_name: str) -> str:
    return f"repos/{repo_name}/"


def _s3_has_objects(s3, bucket: str, prefix: str) -> bool:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return bool(resp.get("Contents"))


def _upload_dir_to_s3(s3, local_dir: Path, bucket: str, prefix: str) -> None:
    for file in local_dir.rglob("*"):
        if file.is_file() and ".git" not in file.parts:
            key = prefix + str(file.relative_to(local_dir)).replace("\\", "/")
            s3.upload_file(str(file), bucket, key)


def _download_s3_prefix(s3, bucket: str, prefix: str, local_dir: Path) -> None:
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            relative = obj["Key"][len(prefix):]
            if not relative:
                continue
            dest = local_dir / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(dest))


def _resolve_repo_path(repo_path: str) -> Path:
    """
    Accepts:
      - GitHub URL  → check S3 cache → else clone → upload to S3 → return local path
      - s3://...    → download from S3 → return local path
      - local path  → return as-is
    """
    s3 = boto3.client("s3", region_name=_AWS_REGION) if _S3_REPO_BUCKET else None

    # ── GitHub URL ──────────────────────────────────────────────────────────
    is_github = repo_path.startswith("https://github.com") or repo_path.startswith("git@github.com")
    if is_github:
        repo_name = _repo_name_from_url(repo_path)
        local_dir = _REPOS_CACHE_DIR / repo_name

        # S3 cache hit — download instead of clone
        if s3 and not local_dir.exists():
            prefix = _s3_prefix(repo_name)
            if _s3_has_objects(s3, _S3_REPO_BUCKET, prefix):
                log.info("s3_cache_hit_downloading", repo=repo_name)
                local_dir.mkdir(parents=True, exist_ok=True)
                _download_s3_prefix(s3, _S3_REPO_BUCKET, prefix, local_dir)
                return local_dir

        # Clone from GitHub
        if not local_dir.exists():
            log.info("cloning_repo", url=repo_path)
            local_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth=1", repo_path, str(local_dir)],
                check=True, capture_output=True,
            )
            log.info("clone_complete", local_dir=str(local_dir))

            # Upload to S3 for future cache hits
            if s3 and _S3_REPO_BUCKET:
                prefix = _s3_prefix(repo_name)
                log.info("uploading_to_s3", bucket=_S3_REPO_BUCKET, prefix=prefix)
                _upload_dir_to_s3(s3, local_dir, _S3_REPO_BUCKET, prefix)
                log.info("s3_upload_complete", repo=repo_name)
        else:
            log.info("local_cache_hit", local_dir=str(local_dir))

        return local_dir

    # ── S3 URL ───────────────────────────────────────────────────────────────
    if repo_path.startswith("s3://"):
        without_scheme = repo_path[5:]
        bucket, _, key = without_scheme.partition("/")
        local_dir = _REPOS_CACHE_DIR / key.replace("/", "_").replace(".", "_")
        if local_dir.exists():
            return local_dir
        local_dir.mkdir(parents=True, exist_ok=True)
        s3c = boto3.client("s3", region_name=_AWS_REGION)
        filename = key.rstrip("/").split("/")[-1]
        if key.endswith("/") or "." not in filename:
            _download_s3_prefix(s3c, bucket, key, local_dir)
        else:
            archive = local_dir / filename
            s3c.download_file(bucket, key, str(archive))
            if filename.endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(local_dir)
            elif filename.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
                with tarfile.open(archive) as tf:
                    tf.extractall(local_dir)
            archive.unlink()
            extracted = [p for p in local_dir.iterdir() if p.is_dir()]
            if len(extracted) == 1:
                local_dir = extracted[0]
        return local_dir

    # ── Local path ───────────────────────────────────────────────────────────
    return Path(repo_path) if repo_path else Path(".")


class CreateSessionRequest(BaseModel):
    user_id: str
    repo_path: str = ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/sessions")
async def create_session(req: CreateSessionRequest):
    session_id = str(uuid.uuid4())
    memory = BedrockMemoryTool(memory_id=_BEDROCK_MEMORY_ID, region=_AWS_REGION)

    try:
        local_path = _resolve_repo_path(req.repo_path)
    except ClientError as e:
        raise HTTPException(status_code=400, detail=f"S3 error: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to resolve repo: {e}")

    state = SessionState(
        session_id=session_id,
        repo_path=local_path,
        user_id=req.user_id,
    )
    _sessions[session_id] = state
    memory.save_session(session_id=session_id, user_id=req.user_id, repo_path=req.repo_path)
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
    log.info("session_deleted", session_id=session_id)
    return {"ok": True}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    state = _sessions.get(session_id)
    memory = BedrockMemoryTool(memory_id=_BEDROCK_MEMORY_ID, region=_AWS_REGION)

    if not state:
        # Attempt to reconstruct from Bedrock memory (survives server restarts)
        try:
            resp = memory._client.retrieve_memory_records(
                memoryId=_BEDROCK_MEMORY_ID,
                namespace=f"/__session__{session_id}/",
                searchCriteria={"searchQuery": f"session_id={session_id}", "topK": 1},
            )
            summaries = resp.get("memoryRecordSummaries", [])
        except Exception:
            summaries = []

        if summaries:
            text = summaries[0].get("content", {}).get("text", "")
            parts = dict(p.split("=", 1) for p in text.split() if "=" in p)
            state = SessionState(
                session_id=session_id,
                repo_path=Path(parts.get("repo_path", ".")),
                user_id=parts.get("user_id", "unknown"),
            )
            _sessions[session_id] = state
            log.info("session_reconstructed", session_id=session_id)
        else:
            await websocket.send_json({"type": "error", "message": "Session not found"})
            await websocket.close()
            return
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

            # Prepend repo path so orchestrator knows what to index
            grounded_text = f"[Repository path: {state.repo_path}]\n\n{text}"

            ctx = RunContext(
                agent_name="orchestrator",
                system_prompt=orchestrator_spec.system_prompt,
                tools=[],
                model=_MODEL_ID,
                session_id=f"{state.user_id}:{state.repo_path}",
                max_usd=_MAX_USD,
                max_turns=_MAX_TURNS,
                websocket=websocket,
                resume_id=state.sdk_session_id or None,
            )

            harness = AgentHarness(
                memory_tool=memory,
                tracer=_tracer,
                mcp_server_path=_MCP_SERVER_PATH,
            )

            try:
                result = await harness.run(grounded_text, ctx)
                # Persist the SDK session id so the next message resumes this conversation
                new_sid = getattr(result, "sdk_session_id", "") or ""
                if new_sid:
                    state.sdk_session_id = new_sid
                state.history.append({"role": "user", "content": text})
                state.history.append({"role": "assistant", "content": result.final_text})

                await websocket.send_json({
                    "type": "done",
                    "cost_usd": round(result.cost_usd, 4),
                    "turns": result.turns,
                })

            except Exception as e:
                log.error("harness_error", session_id=session_id, error=repr(e), exc_info=True)
                await websocket.send_json({"type": "error", "message": str(e) or repr(e)})

    except WebSocketDisconnect:
        log.info("ws_disconnected", session_id=session_id)
