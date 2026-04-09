# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nanoCursor** is a multi-agent automatic programming framework built on LangGraph and Docker. It transforms user requests into working code through four specialist agents: Planner, Coder, Sandbox, and Reviewer, connected in a state-machine workflow with automatic repair loops.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest

# Run a single test file
pytest tests/test_file_tools.py

# Start the web application (React frontend + FastAPI backend)
python api_server.py

# Run CLI (executes hardcoded prompt in run.py)
python run.py

# Streamlit Web UI (alternative to React frontend)
streamlit run web_ui.py
```

Frontend development mode:
```bash
cd frontend
npm install    # only needed once
npm run dev    # starts Vite dev server on port 3000, proxies /api to localhost:8100
```

Docker Desktop must be running for sandbox functionality.

## Architecture

### Tech Stack
- **Python 3.10+** with **LangGraph** (~1.0.x) for agent orchestration via `StateGraph`
- **LLM providers**: OpenAI, Anthropic, Ollama, DeepSeek (configured via `.env`)
- **Docker SDK** for sandbox isolation (`python:3.10-slim`, no network, 256MB memory limit)
- **Pydantic v2** for structured output parsing
- **FastAPI + uvicorn** for the backend API + SSE streaming
- **React 18 + TypeScript + Vite** for the frontend
- **python-dotenv** for config; config lives in `src/core/.env` (gitignored)

### Key Files

| File | Purpose |
|------|---------|
| `api_server.py` | FastAPI backend: REST + SSE endpoints, serves React frontend |
| `run.py` | LangGraph `StateGraph` builder & CLI entry point |
| `web_ui.py` | Streamlit Web UI (alternative frontend) |
| `frontend/` | Vite + React + TypeScript frontend app |
| `frontend/src/pages/ChatPage.tsx` | Chat page with SSE-driven live updates |
| `frontend/src/pages/MetricsPage.tsx` | Metrics dashboard |
| `frontend/src/pages/FileBrowserPage.tsx` | File browser (workspace/backups/snapshots) |
| `frontend/src/pages/ConfigPage.tsx` | Configuration panel |
| `src/agents/Planner.py` | Planner node: explores workspace, generates development plan |
| `src/agents/Coder.py` | Coder node: reads/edits/writes files |
| `src/agents/Sandbox.py` | Sandbox node: Docker container execution |
| `src/agents/Reviewer.py` | Reviewer node: analyzes sandbox error output |
| `src/core/state.py` | `AgentState` TypedDict -- shared blackboard + `InMemorySaver` checkpointer |
| `src/core/llm_engine.py` | Multi-provider LLM init + retry wrapper with exponential backoff |
| `src/core/context_manager.py` | v2.0 layered context management (core/working/reference memory) |
| `src/core/config.py` | Global config: path resolution (`WORKSPACE_DIR`), env bootstrap, all tunable constants |
| `src/core/logger.py` | Structured logger factory |
| `src/core/repo_map.py` | AST-based function/class signature extraction |
| `src/core/routing.py` | Routing decision logic for `route_after_planner` and `route_after_coder` |
| `src/core/metrics.py` | Thread-safe `MetricsCollector` singleton tracking LLM calls/tokens/latency, tool success rate, repair cycle outcomes |
| `src/core/recovery.py` | Workspace snapshot system on circuit breaker |
| `src/tools/file_tools.py` | 8 file tools: read, write, edit (fuzzy match), read_function, read_class, read_file_range, backup/rollback + `list_directory` |

### Frontend Architecture

The React frontend uses a sidebar layout with four pages:

1. **工作台 (Chat)**: Chat interface + real-time SSE updates from LangGraph workflow + execution log + workflow node diagram
2. **指标面板 (Metrics)**: LLM calls, tokens, latency, tool success rate, repair cycles
3. **文件浏览器 (Files)**: File tree + syntax-highlighted viewer + backups/snapshots tabs
4. **配置面板 (Config)**: LLM provider status cards + system config table + environment variables

Global state is managed via React Context (`AppContext.tsx`). SSE events from the backend drive real-time UI updates through the `EventSource` API.

### Backend API (api_server.py)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/run` | Start workflow with prompt, return `{thread_id, status}` |
| `GET /api/run/{thread_id}/events` | SSE stream of LangGraph node events |
| `GET /api/run/{thread_id}/state` | Get final state via `graph_app.get_state()` |
| `GET /api/files` | Workspace file tree |
| `GET /api/files/{path}` | Read file content |
| `GET /api/metrics` | Metrics dump + historical |
| `GET /api/config` | LLM provider status, system config, env vars |
| `GET /api/snapshots` | List recovery snapshots |
| `GET /api/backups` | List backup files |

SSE uses background thread running `graph_app.stream()` pushing to per-thread `Queue`.

### Workflow Graph (from `run.py`)

- **route_after_planner**: Tool calls -> `planner_tools` -> back to Planner. No tool calls -> Coder.
- **route_after_coder**: Tool calls -> increment `coder_step_counter` -> `coder_tools` -> back to Coder. No tool calls or `coder_step_count >= max_coder_steps` (default 15) -> Sandbox.
- **route_after_sandbox**: No error -> END. Error + retry < max -> Reviewer -> Coder (repair loop). Retry >= max -> snapshot via recovery.py -> END (circuit breaker).

### Context Management (v2.0)

`context_manager.py` implements a three-layer strategy:
1. **Core memory**: User request, plan, error traces (permanent)
2. **Working memory**: Recent N turns (sliding window, dynamically adjustable)
3. **Reference memory**: LLM-driven structured summary of older history

Uses `tiktoken` (`cl100k_base`) for precise token counting.

### File Operations

`file_tools.py` provides 8 tools with three-tier edit matching: exact match -> stripped match -> fuzzy `difflib` match at 90% threshold. Large files (>5000 chars) are read via AST outline. All paths are validated against traversal attacks.

## Configuration

Config is in `src/core/.env` (gitignored), see `.env.example` for template. Supports `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, `DEEPSEEK_API_KEY` for different LLM providers.
