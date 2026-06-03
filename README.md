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
        ├── main.py             # FastAPI app, session management, WebSocket
        ├── harness.py          # Bedrock agentic loop + tool dispatch
        ├── context.py          # RunContext, RunResult, SessionState
        ├── dispatcher.py       # routes tool_use blocks to handlers
        ├── result.py           # shared ToolResult type
        ├── agents/
        │   ├── registry.py     # AgentSpec definitions
        │   └── prompts.py      # system prompts for each agent
        ├── tools/
        │   ├── mcp_client.py   # spawns & speaks to the MCP server
        │   ├── first_party.py  # read_file, grep, list_dir
        │   ├── bedrock_memory.py  # Bedrock Agent Core Memory
        │   └── delegate.py     # sub-agent delegation tool
        └── observability/
            ├── tracer.py       # Langfuse tracing
            └── logging.py      # structlog configuration
```

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

The investigator is a **FastAPI** service. Clients open a WebSocket connection and send natural-language questions. The service runs an agentic loop using **Claude on AWS Bedrock**, dispatching tool calls to either the MCP server or built-in tools, then streams tokens and tool events back to the client.

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

| Agent | Role | Tools |
|-------|------|-------|
| `orchestrator` | Coordinates investigation, answers the user | All MCP tools + read_file + grep + list_dir + memory + delegate |
| `code_explorer` | Traces call graphs, finds definitions | All MCP tools + file ops + raw Cypher |
| `test_analyst` | Maps tests to code, finds gaps | get_test_coverage, find_references, grep, read_file |
| `security_auditor` | Data-flow tracing, dangerous patterns, hardcoded secrets | find_path, find_references, grep, read_file |
| `doc_synthesizer` | Rewrites findings into structured prose | No tools (synthesis only) |

The orchestrator starts every session by calling `memory_search` ("have I investigated this repo before?") and ends by calling `memory_save` with key findings. Sub-agents are spawned via `delegate_to_subagent` when sustained focus in one domain is needed.

**Cost and turn guards**

`RunContext` tracks token spend against `MAX_USD_PER_QUERY` (default `$0.50`) and `MAX_TURNS` (default `25`). Each sub-agent gets at most one-third of the parent's remaining budget. The loop stops and returns a `budget_exceeded` result if either limit is hit.

---

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- AWS credentials with Bedrock access (Claude model + Bedrock Agent Core Memory)

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
| `BEDROCK_MODEL_ID` | — | Cross-region inference profile ARN, e.g. `us.anthropic.claude-sonnet-4-6-20250514-v2:0` |
| `BEDROCK_MEMORY_ID` | — | Bedrock Agent Core Memory resource ID |
| `MCP_SERVER_PATH` | `../mcp-server-code-graph` | Path to the MCP server directory |
| `MAX_USD_PER_QUERY` | `0.50` | Per-query cost cap in USD |
| `MAX_TURNS` | `25` | Max agentic turns per query |
| `LANGFUSE_PUBLIC_KEY` | — | Optional — Langfuse tracing |
| `LANGFUSE_SECRET_KEY` | — | Optional — Langfuse tracing |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Optional — Langfuse host |
| `ENV` | `dev` | Set to `prod` to disable dev logging |

The `BEDROCK_MODEL_ID` must be a cross-region inference profile ARN. Find the correct ARN for your region in the [AWS Bedrock inference profiles documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html).

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

### Investigator tests

Unit tests cover the agentic harness (`test_harness.py`), context budget logic (`test_context.py`), tool dispatcher (`test_dispatcher.py`), MCP client (`test_mcp_client.py`), first-party tools (`test_first_party.py`), Bedrock memory (`test_bedrock_memory.py`), agent registry (`test_registry.py`), tracer (`test_tracer.py`), delegation (`test_delegate.py`), and the FastAPI endpoints (`test_main.py`).

---

## Observability

Tracing is built on **Langfuse**. Each `harness.run()` call opens a root trace; each agentic turn and tool batch opens a child span annotated with token counts, cost, and stop reason. If `LANGFUSE_PUBLIC_KEY` is not set, the tracer becomes a no-op and nothing breaks.

Structured logging uses **structlog** in JSON mode in production and human-readable dev mode.

---

## License

See [LICENSE](LICENSE).
