# nanoCursor

**A multi-agent automatic programming framework built on LangGraph and Docker.**

nanoCursor transforms natural language requests into working code through four specialized agents connected in a state-machine workflow — with automatic repair loops when tests fail.

---

## How it works

```
User Request
      │
      ▼
┌─────────┐  plan done   ┌─────────┐  tools   ┌─────────────────┐
│ Planner │─────────────►│  Coder  │────────►│ coder_tools     │
│ (architect)            │ (engineer)            │ (edit/read/write)│
└─────────┘              └─────────┘◄─────────┴─────────────────┘
                              │
                              │ done / max_steps
                              ▼
                        ┌─────────┐  error      ┌──────────┐  max_retries
                        │ Sandbox │────────────►│ Reviewer │
                        │(test/run)│             │(diagnose)│──────────► Coder
                        └─────────┘             └──────────┘
                              │ OK
                              ▼
                             END
```

**The loop:** Sandbox runs tests → if they fail, Reviewer analyzes the error and sends Coder back to fix → repeat until passing or circuit breaker hits.

---

## Features

### Four specialized agents

| Agent | Role | What it does |
|-------|------|-------------|
| **Planner** | Architect | Explores workspace, understands requirements, generates execution plan |
| **Coder** | Engineer | Reads/edits files, executes code modifications |
| **Sandbox** | Test Runner | Docker-isolated execution, auto-discovers `test_*.py`, runs pytest |
| **Reviewer** | Diagnostician | Analyzes error traces + diffs, generates fix suggestions |

### Three-layer memory management (v2.0)

| Layer | Content | Strategy |
|-------|---------|----------|
| **Core** | User request, plan, error traces | Permanent, never evicted |
| **Working** | Recent N turns | Sliding window, dynamically adjustable |
| **Reference** | LLM-generated summary of older history | Smart compression via tiktoken |

Uses `cl100k_base` encoding for precise token counting. Auto-fallback to estimation when unavailable.

### Docker sandbox isolation

- No network, 256MB memory limit, 60s timeout
- Auto-discovers and runs `test_*.py` / `*_test.py`
- Installs `requirements.txt` automatically if present
- `auto_remove=True` prevents container leakage

### AST-aware file operations

- Large files (>5000 chars) return AST outline instead of raw content
- `read_function` / `read_class` / `read_file_range` for precise targeting
- `edit_file` with three-tier matching:
  1. Exact match (原文一字不差)
  2. Strip-match (whitespace stripped)
  3. Fuzzy match via difflib (90% threshold, rejects below)

### Observability

- **MetricsCollector** — thread-safe: LLM calls/tokens/latency, tool success rate, repair cycle outcomes
- **Recovery snapshots** — on circuit breaker: packages workspace `.py` files + active_files + mod log to `workspace/.snapshots/`
- **Per-node loggers** — structured logging for easy debugging

### Type-safe API layer

All endpoints use Pydantic BaseModel request/response schemas:

```python
RunRequest(prompt: str, thread_id: str | None)
CancelResponse(cancelled: bool, thread_id: str)
AgentStateResponse(messages: list[Message], current_plan: str | None, ...)
FileListResponse(files: list[FileEntry])
MetricsResponse(current: MetricsCurrentResponse, historical: list[...])
ConfigResponse(llm_providers: dict, system: SystemConfig, env_vars: list[EnvVar])
```

### Persistent checkpoints

LangGraph state persisted to SQLite via `langgraph-checkpoint-sqlite`:

- **Session resume** — same `thread_id` continues where you left off
- **Concurrency control** — 429 if a run is already active on that thread
- **Rate limiting** — 10s cooldown between starts on the same thread

Falls back to `InMemorySaver` if the package is not installed.

---

## Quick start

### Requirements

- Python 3.10+
- Docker Desktop (running)
- One LLM provider: OpenAI / Anthropic / Ollama / DeepSeek

### Installation

```bash
git clone https://github.com/MagicalLiHua/nanoCursor.git
cd nanoCursor
pip install -r requirements.txt
```

### Configuration

Create `src/core/.env` (or copy from `.env.example`):

```bash
# Option 1: Ollama (local, free)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder

# Option 2: OpenAI
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o

# Option 3: Anthropic
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_MODEL=claude-3-5-sonnet-latest

# Option 4: DeepSeek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat
```

### Run

```bash
# Web UI (recommended)
streamlit run web_ui.py

# Or: API server + React frontend
python api_server.py   # starts on http://localhost:8100
# then in another terminal:
cd frontend && npm install && npm run dev

# Or: CLI (hardcoded prompt in run.py)
python run.py
```

### Sandbox requirements

Docker Desktop must be running. The sandbox uses `python:3.10-slim` image with no network and 256MB memory limit.

---

## Configuration reference

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_IMAGE` | `python:3.10-slim` | Docker image for sandbox |
| `SANDBOX_MEM_LIMIT` | `256m` | Container memory limit |
| `SANDBOX_TIMEOUT_SECONDS` | `60` | Max execution time |
| `MAX_CODER_STEPS` | `15` | Coder max tool-call steps per turn |
| `MAX_PLANNER_STEPS` | `10` | Planner max exploration steps |
| `LARGE_FILE_THRESHOLD` | `5000` | Characters before switching to AST outline |
| `FUZZY_MATCH_THRESHOLD` | `0.9` | Minimum edit similarity to accept |
| `MAX_FUZZY_MATCH_LINES` | `2000` | Skip fuzzy match above this line count |
| `LLM_TEMPERATURE` | `0.2` | LLM sampling temperature |
| `LLM_MAX_TOKENS` | `4096` | Max output tokens |

### Context manager config (in `context_manager.py`)

```python
DEFAULT_CONFIG = {
    "max_context_tokens": 8000,
    "coder_keep_turns": 4,
    "planner_keep_turns": 3,
    "reviewer_keep_turns": 2,
    "system_prompt_tokens": 800,
    "error_trace_tokens": 500,
}
```

---

## Project structure

```
nanoCursor/
├── api_server.py              # FastAPI backend (REST + SSE)
├── run.py                     # LangGraph StateGraph builder + CLI entry
├── web_ui.py                  # Streamlit alternative frontend
├── requirements.txt           # Python dependencies
│
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   └── models.py          # 26 Pydantic request/response models
│   ├── agents/
│   │   ├── Planner.py          # Plan generation + workspace exploration
│   │   ├── Coder.py           # File editing + code modification
│   │   ├── Sandbox.py         # Docker container lifecycle + test execution
│   │   └── Reviewer.py        # Error analysis + repair diagnosis
│   ├── core/
│   │   ├── config.py          # Path resolution + env bootstrap
│   │   ├── state.py           # AgentState TypedDict + SqliteSaver checkpointer
│   │   ├── llm_engine.py      # Multi-provider LLM init + LLMWithRetry wrapper
│   │   ├── context_manager.py # Three-layer memory (core/working/reference)
│   │   ├── routing.py         # route_after_planner/coder/sandbox decisions
│   │   ├── metrics.py         # MetricsCollector singleton
│   │   ├── recovery.py        # Snapshot system on circuit breaker
│   │   ├── logger.py          # Structured logger factory
│   │   └── repo_map.py        # AST-based function/class signature extraction
│   └── tools/
│       └── file_tools.py      # 8 tools: read, write, edit, read_function,
│                              #   read_class, read_file_range, backup, rollback
├── tests/                     # 97 pytest tests (51% coverage)
├── frontend/                  # Vite + React + TypeScript
│   └── src/pages/
│       ├── ChatPage.tsx       # SSE-driven live workflow updates
│       ├── MetricsPage.tsx    # LLM calls / tokens / latency / repair cycles
│       ├── FileBrowserPage.tsx # Workspace file tree + syntax highlighting
│       └── ConfigPage.tsx     # LLM provider status + env vars
├── .github/workflows/ci.yml  # GitHub Actions: lint + test + frontend + audit
├── .pre-commit-config.yaml   # black, isort, ruff, mypy hooks
└── pyproject.toml             # pytest, coverage (50% min), ruff, mypy config
```

---

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/run` | Start workflow with `RunRequest(prompt, thread_id?)` |
| `GET` | `/api/run/{thread_id}/events` | SSE stream of node events |
| `POST` | `/api/run/{thread_id}/cancel` | Cancel running workflow |
| `GET` | `/api/run/{thread_id}/state` | Get final AgentState |
| `GET` | `/api/files` | List workspace files |
| `GET` | `/api/files/{path}` | Read file content |
| `GET` | `/api/metrics` | Current + historical metrics |
| `GET` | `/api/config` | LLM providers, system config, env vars |
| `GET` | `/api/snapshots` | List recovery snapshots |
| `GET` | `/api/snapshots/{id}` | Get snapshot detail |
| `GET` | `/api/backups` | List backup files |
| `GET` | `/api/backups/{name}` | Read backup content |

---

## Development

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov=api_server --cov=run

# Lint
ruff check src/ api_server.py

# Type check
mypy src/

# Pre-commit (lint + type check + tests before commit)
pre-commit run --all-files
```

---

## Architecture highlights

### Workflow cancellation

Each node checks `state.cancelled` at entry. Setting it to `True` via `graph_app.update_state()` causes all in-progress nodes to raise `WorkflowCancelledError`, terminating the workflow cleanly.

### Three-tier edit matching

When `edit_file` can't find the exact search block, it tries progressively more lenient strategies:

```
1. Exact    → search_block matches exactly
2. Stripped → whitespace trimmed before matching
3. Fuzzy    → difflib sliding window, 90% similarity required
               below threshold: reject + prompt user to re-read
```

### Circuit breaker

When Sandbox reports error and `retry_count >= max_retries`:
1. Recovery system snapshots all `.py` files + active files + mod log
2. Workflow terminates with diagnostic info
3. User can inspect snapshot contents and manually continue

---

## Roadmaps

- [ ] **libcst / Tree-sitter** — replace text Search/Replace with AST-level edits
- [ ] **Repo-level RAG** — vectorized code search for large repositories
- [ ] **Linter pre-check** — LSP/Linter before sandbox to skip obvious errors
- [ ] **LLM Circuit Breaker** — fail fast after N consecutive LLM failures
- [ ] **SWE-bench** —量化 Pass Rate, avg token cost metrics

---

## Dependencies

| Package | Purpose |
|---------|---------|
| langgraph ≥1.0.0 | State-machine / agent orchestration |
| langchain-core ≥1.2.0 | LLM message / tool abstraction |
| langchain-openai / anthropic / ollama | Provider adapters |
| pydantic ≥2.0.0 | Structured I/O validation |
| tiktoken ≥0.9.0 | Precise token counting (cl100k_base) |
| docker ≥7.0.0 | Sandbox container management |
| fastapi ≥0.115.0 | REST API + SSE |
| langgraph-checkpoint-sqlite ≥2.0.0 | Persistent workflow state |

---

## License

MIT

---

Made by [LiHua](https://github.com/MagicalLiHua)