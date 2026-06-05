# code-graph

A codebase intelligence system: an MCP server that builds a structural graph of your code, paired with an AI investigator that answers questions about it using Claude on AWS Bedrock.

```
┌──────────────────────────────────────────────────┐
│  investigator (FastAPI + WebSocket)               │
│  ┌────────────┐  ┌──────────────────────────┐    │
│  │ orchestrator│  │ sub-agents               │    │
│  │ (Claude)    │──│ code_explorer            │    │
│  │             │  │ test_analyst             │    │
│  └─────┬───────┘  │ security_auditor         │    │
│        │          │ doc_synthesizer          │    │
│        │          └──────────────────────────┘    │
│        │                                          │
│        ▼ stdio (MCP JSON-RPC)                     │
│  ┌─────────────────────────────────────────────┐  │
│  │  mcp-server-code-graph                      │  │
│  │  tree-sitter parser → Kùzu graph DB         │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

---

## Repository layout

```
code-graph/
├── mcp-server-code-graph/      # MCP stdio server
│   └── src/code_graph_mcp/
│       ├── server.py           # MCP tool definitions + dispatch
│       ├── indexer.py          # tree-sitter parser → ExtractedFile
│       ├── query.py            # all query functions
│       ├── schema.py           # Kùzu DDL (nodes, edges, version)
│       ├── watcher.py          # live file-system watcher
│       └── result.py           # ToolResult envelope
└── investigator/               # AI agent backend
    └── src/investigator/
        ├── main.py             # FastAPI app, session management, WebSocket, repo resolution (GitHub/S3/local)
        ├── harness.py          # Claude Agent SDK wiring (query loop, subagents, MCP, memory, tracing)
        ├── context.py          # RunContext, RunResult, SessionState
        ├── result.py           # shared ToolResult type
        ├── agents/
        │   ├── registry.py     # AgentSpec definitions
        │   └── prompts.py      # system prompts for each agent
        ├── tools/
        │   └── bedrock_memory.py  # Bedrock AgentCore Memory (exposed as in-process MCP tools)
        └── observability/
            ├── tracer.py       # Langfuse tracing
            └── logging.py      # structlog configuration
```

> The agent loop, tool dispatch, sub-agent delegation, built-in file tools
> (Read/Grep/Glob), and session state are provided by the **Claude Agent SDK**
> (`claude_agent_sdk.query`). The harness wires our domain into it; it no longer
> hand-rolls the loop, a tool dispatcher, an MCP client, or first-party file tools.

---

## How it works

### 1. Code graph (mcp-server-code-graph)

The MCP server parses Python (`.py`) and TypeScript/TSX (`.ts`, `.tsx`) files using **tree-sitter** and stores the result in an embedded **Kùzu** graph database.

**Graph schema**

| Node | Primary key | Stored fields |
|------|-------------|---------------|
| `File` | `path` | language, loc, hash |
| `Class` | `qualified_name` | name, file_path, line_start/end, docstring |
| `Function` | `qualified_name` | name, file_path, line_start/end, signature, docstring, is_async |
| `Method` | `qualified_name` | same as Function + class_qualified_name |
| `Test` | `qualified_name` | name, file_path, framework, line_start/end |
| `Import` | `id` | from_module, imported_name, alias, file_path, line |
| `IndexMeta` | `id` | schema_version, last_indexed_at, file_count |

| Edge | Meaning |
|------|---------|
| `CONTAINS` | File → Class / Function |
| `DEFINES` | Class → Method |
| `CALLS` | Function/Method → Function/Method |
| `INHERITS` | Class → Class (superclass) |
| `TESTS` | Test → Function/Method |
| `IMPORTS` | File → Module |
| `REFERENCES` | Function/Method → Variable |
| `DECORATES` | Function/Method → Function |

**Incremental indexing**

`RepoWatcher` (`watcher.py`) uses `watchdog` to listen for file-system events and re-indexes modified or created files without a full rebuild.

**MCP tools exposed**

| Tool | Description |
|------|-------------|
| `index_repo` | Parse and index a repository. Auto-rebuilds on schema version mismatch. |
| `index_status` | Return last-indexed timestamp, file count, schema version. |
| `find_symbol` | Find classes, functions, methods, or tests by name. Supports fuzzy match. |
| `get_file_summary` | List all symbols in a file without reading it. |
| `get_callers` | Walk the `CALLS` graph backwards, up to N hops. |
| `get_callees` | Walk the `CALLS` graph forwards, up to N hops. |
| `find_path` | Shortest call path between two qualified names. |
| `find_references` | All callers + inheritors of a symbol. |
| `get_imports` | Import list for a file. |
| `get_test_coverage` | Tests that cover a given function or method. |
| `query_graph` | Raw Cypher query (gated — must pass `enabled=true`). |

All tools return a JSON envelope:

```json
{ "ok": true,  "data": { ... } }
{ "ok": false, "error": "...", "error_code": "NOT_INDEXED" }
```

---

### 2. AI investigator

The investigator is a **FastAPI** service. Clients open a WebSocket connection and send natural-language questions. The service runs the agent loop with the **Claude Agent SDK** (`claude_agent_sdk.query`) on **Claude via AWS Bedrock** (`CLAUDE_CODE_USE_BEDROCK=1`). The SDK owns the loop, tool dispatch, and sub-agent delegation; the harness supplies the orchestrator system prompt, the code-graph MCP server, Bedrock memory as in-process MCP tools, and Langfuse tracing. Tokens and tool events stream back to the client over the WebSocket.

**Session lifecycle**

```
POST /sessions          → { session_id }
WS   /ws/{session_id}   ← send { type: "message", text: "..." }
                        → stream { type: "token", text: "..." }
                              { type: "tool_start", tool: "...", args: {} }
                              { type: "tool_end",   tool: "...", ok: true }
                              { type: "done", cost_usd: 0.02, turns: 4 }
DELETE /sessions/{id}
```

**Agent roster**

Sub-agents are defined as Agent SDK `AgentDefinition`s and invoked through the built-in `Task` tool. Internal tool names map onto SDK tools: `read_file`/`grep`/`list_dir` → built-in `Read`/`Grep`/`Glob`, the code-graph tools → `mcp__codegraph__*`, and `memory_save`/`memory_search` → an in-process `mcp__memory__*` server.

| Agent | Role | Tools |
|-------|------|-------|
| `orchestrator` | Coordinates investigation, answers the user | code-graph MCP tools + Read + Grep + Glob + memory + Task |
| `code_explorer` | Traces call graphs, finds definitions | code-graph MCP tools + Read/Grep/Glob + raw Cypher |
| `test_analyst` | Maps tests to code, finds gaps | get_test_coverage, find_references, Grep, Read |
| `security_auditor` | Data-flow tracing, dangerous patterns, hardcoded secrets | find_path, find_references, Grep, Read |
| `doc_synthesizer` | Rewrites findings into structured prose | No tools (synthesis only) |

The orchestrator starts every session by calling `memory_search` ("have I investigated this repo before?") and ends by calling `memory_save` with key findings. The Agent SDK persists conversation state per session, so follow-up messages resume the prior context.

**Cost and turn guards**

The harness passes `max_budget_usd` (from `MAX_USD_PER_QUERY`, default `$0.50`) and `max_turns` (from `MAX_TURNS`, default `25`) into `ClaudeAgentOptions`. The SDK stops the run when either limit is hit; final cost and turn count come back on the SDK `ResultMessage`.

---

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- The `claude` CLI on `PATH` (required by the Claude Agent SDK)
- AWS credentials with Bedrock access (Claude model + Bedrock AgentCore Memory)
- `git` (used to clone GitHub repos), and optionally an S3 bucket for repo caching

---

## Setup

### 1. MCP server

```bash
cd mcp-server-code-graph
uv sync
```

Run standalone (for testing):

```bash
uv run code-graph-mcp --db-path /path/to/graph.kuzu
```

The server speaks MCP over stdio. Clients launch it as a subprocess.

### 2. Investigator

```bash
cd investigator
uv sync
cp .env.example .env
# fill in .env (see Configuration below)
```

Start the API server:

```bash
uv run uvicorn investigator.main:app --reload --port 8000
```

---

## Configuration

All configuration is via environment variables (`.env` in `investigator/`).

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock |
| `AWS_ACCESS_KEY_ID` | — | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | — | AWS credentials |
| `BEDROCK_MODEL_ID` | — | Cross-region inference profile ARN, e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `BEDROCK_MEMORY_ID` | — | Bedrock AgentCore Memory resource ID |
| `MCP_SERVER_PATH` | `../mcp-server-code-graph` | Path to the MCP server directory |
| `S3_REPO_BUCKET` | — | Optional — S3 bucket used to cache cloned repos |
| `MAX_USD_PER_QUERY` | `0.50` | Per-query cost cap in USD (`max_budget_usd`) |
| `MAX_TURNS` | `25` | Max agentic turns per query (`max_turns`) |
| `LANGFUSE_PUBLIC_KEY` | — | Optional — Langfuse tracing |
| `LANGFUSE_SECRET_KEY` | — | Optional — Langfuse tracing |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Optional — Langfuse host |
| `ENV` | `dev` | Set to `prod` to disable dev logging |

The `BEDROCK_MODEL_ID` must be a cross-region inference profile ID/ARN. Find the correct value for your region in the [AWS Bedrock inference profiles documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html).

The app sets `CLAUDE_CODE_USE_BEDROCK=1` automatically so the Claude Agent SDK routes through Bedrock using the AWS credentials in the environment.

`repo_path` on `POST /sessions` accepts three forms: a **local path**, a **GitHub URL** (cloned with `--depth=1`, then cached to `S3_REPO_BUCKET` if set), or an **`s3://bucket/key`** URL (downloaded and, if an archive, extracted).

---

## API reference

### REST

```
GET  /health                        → { "status": "ok" }
POST /sessions                      body: { user_id, repo_path? }
                                    → { session_id }
GET  /sessions/{id}                 → { session_id, user_id, repo_path, history_length }
POST /sessions/{id}/repo            body: { repo_path }
DELETE /sessions/{id}
```

### WebSocket `/ws/{session_id}`

**Client → Server**

```json
{ "type": "message", "text": "Which functions call parse_python_file?" }
{ "type": "cancel" }
```

**Server → Client**

```json
{ "type": "token",      "text": "The function " }
{ "type": "tool_start", "tool": "find_symbol", "args": { "name": "parse_python_file" } }
{ "type": "tool_end",   "tool": "find_symbol", "ok": true }
{ "type": "done",       "cost_usd": 0.018, "turns": 3 }
{ "type": "error",      "message": "..." }
```

---

## Development

### Run tests

```bash
# MCP server
cd mcp-server-code-graph
uv run pytest

# Investigator
cd investigator
uv run pytest
```

### MCP server fixtures

Test fixtures live in `mcp-server-code-graph/tests/fixtures/`. The test suite covers indexing (`test_indexer.py`), all query functions (`test_query.py`), the result envelope (`test_result.py`), the schema (`test_schema.py`), the server tool dispatch (`test_server.py`), and the file watcher (`test_watcher.py`).

Unit tests cover context budget logic (`test_context.py`), the result envelope (`test_result.py`), Bedrock memory (`test_bedrock_memory.py`), the agent registry (`test_registry.py`), the tracer (`test_tracer.py`), and the FastAPI endpoints (`test_main.py`). End-to-end agent behaviour is driven by the Claude Agent SDK and exercised by running the server.

---

## Observability

Tracing is built on **Langfuse**. Each `harness.run()` call opens one root agent trace; every tool call the SDK reports opens a nested child span with its input and output. The root trace is annotated with the model, turn count, total cost, the list of tools called, and the SDK session id. If `LANGFUSE_PUBLIC_KEY` is not set, the tracer becomes a no-op and nothing breaks.

Structured logging uses **structlog** in JSON mode in production and human-readable dev mode.

---

## License

See [LICENSE](LICENSE).
