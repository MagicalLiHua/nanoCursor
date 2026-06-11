# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nanoCursor** is a local AI coding workspace with a React frontend and FastAPI backend. It transforms user requests into observable coding runs through a Lead-first agent loop, tool calling, task state, approvals, event streaming, and recovery records.

## Project Type

Personal independent full-stack project.

## Development Rules

- Inspect related files before editing.
- Prefer small, safe, incremental changes.
- Use existing project patterns.
- Do not rewrite large unrelated areas.
- Do not run destructive commands without explicit confirmation.
- Do not modify production database.
- Use Context7 for version-sensitive or unfamiliar library APIs.
- Use Playwright or a frontend smoke check when UI behavior changes.
- Run relevant checks after meaningful changes.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run tests with coverage (target: ≥50%)
pytest --cov

# Run a single test file
pytest tests/test_file_tools.py

# Lint (ruff)
ruff check .

# Type check (mypy)
mypy src/

# Frontend check
cd frontend && npm run check

# Start web backend
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8100

# Start web frontend (separate terminal)
cd frontend && npm run dev
```

## Architecture

### Core Engine

The current backend is a FastAPI + Agent Runtime split:
- `src/api/server.py` is the public ASGI entrypoint.
- `src/api/app.py` builds the FastAPI app and registers modular routers.
- The old root `api_server.py` shim has been removed; compatibility exports live in `src/api/legacy_runtime.py`.
- `src/api/services/runtime_registry_service.py` owns the process-wide RunManager and EventStore.
- `src/api/services/runtime_lifecycle_service.py` owns startup recovery, cleanup, and shutdown persistence.
- `src/api/services/runtime_executor_service.py` owns the core workflow executor.
- `src/api/services/workflow_thread_service.py` is the only allowed caller of the remaining workflow compatibility adapters.
- `src/api/services/deterministic_run_service.py` owns demo/benchmark worker finalization.
- `src/api/legacy_runtime.py` now owns only compatibility wrappers, old monkeypatch exports, and production static serving.
- `src/agent/engine.py` contains the model/tool adapter and low-level agent loop.
- `src/api/services/runtime_turn_service.py` and `src/api/services/agent_loop_controller_service.py` contain the newer controlled loop path.

### Key Files

| File | Purpose |
|------|---------|
| `src/api/server.py` | Public ASGI entrypoint |
| `src/api/app.py` | FastAPI app factory and router registration |
| `src/api/legacy_runtime.py` | Compatibility wrapper for legacy imports and static serving |
| `src/api/services/runtime_executor_service.py` | Core Agent workflow executor |
| `src/api/services/runtime_registry_service.py` | Single owner of active run state |
| `src/api/services/runtime_lifecycle_service.py` | Shared FastAPI runtime lifecycle |
| `src/api/services/workflow_thread_service.py` | Single boundary for workflow start/resume/retry/remediation threads |
| `src/api/services/deterministic_run_service.py` | Shared demo/benchmark worker lifecycle |
| `frontend/src/` | React + Vite 前端工作台 |
| `src/agent/engine.py` | Model/tool adapter and low-level agent loop |
| `src/agent/state.py` | AgentState + WorkflowCancelledError |
| `src/agent/prompt_builder.py` | Runtime system prompt construction |
| `src/agent/learner.py` | Agent 学习器（从运行中学习） |
| `src/api/models.py` | API Pydantic 数据模型 |
| `src/api/routes/` | FastAPI route modules |
| `src/api/services/` | Backend service layer for runs, conversations, context, tools, recovery, quality and evals |
| `src/indexer/indexer.py` | 项目索引器 |
| `src/api/services/memory_governance_service.py` | Governed memory 的唯一存储与生命周期入口 |
| `src/api/services/memory_selection_service.py` | 按作用域、相关性和预算选择记忆 |
| `src/tasks/manager.py` | 早期任务池工具 |
| `src/tools/file_tools.py` | 文件操作 |
| `src/tools/bash.py` | Bash 命令执行 |
| `src/tools/git_tools.py` | Git 操作 |
| `src/tools/memory_tools.py` | 记忆 CRUD 工具 |
| `src/tools/project_tools.py` | 项目级工具 |
| `src/infra/config.py` | 配置管理 |
| `src/infra/llm_config.py` | LLM 提供商配置 |
| `src/infra/hooks.py` | 事件钩子系统 |
| `src/infra/background.py` | 后台任务管理 |
| `src/infra/cron.py` | 定时任务调度 |
| `src/infra/worktree.py` | Git worktree 隔离 |
| `src/infra/permission.py` | 权限管道 + Bash 安全验证 |
| `src/infra/metrics.py` | MetricsCollector |
| `src/infra/schemas.py` | 共享 Pydantic schemas |
| `src/infra/messages.py` | 消息流管理 |

### Tech Stack
- **Python 3.10+** with **asyncio** for async agent orchestration
- **LLM providers**: DeepSeek, MiniMax (Anthropic-compatible), OpenAI, Anthropic, Ollama
- **Pydantic v2** for structured output parsing
- **FastAPI + uvicorn** for the backend API
- **React + Vite** for the frontend workspace
- **python-dotenv** for config

### Core Concepts

**Agent Loop**: The core engine (`agent_loop()`) is a simple while loop:
1. Send messages to LLM with tool definitions
2. If `stop_reason == "tool_use"`, process tool calls and continue
3. Otherwise return the final text response

**Tool Calling**: Tools are defined in Anthropic `input_schema` format. Agent calls tools via `client.messages.create()`.

**Subagents**: `run_subagent()` spawns independent agent contexts for isolated tasks.

**Team System**: Persistent autonomous teammates with JSONL inbox communication:
- `spawn_agent`: Launch a run-scoped temporary specialist with a constrained tool set
- `gather_agents`: Wait for asynchronous specialists and merge their results
- `task_create`, `task_update`, `task_list`: Coordinate work through the shared task board
- Legacy teammate messaging and Todo tools are retired from the model-facing runtime

**Task System**: JSON file-based task persistence in `.tasks/` directory with task dependency support.

**Memory System**: Workspace-scoped governed memory with explicit scope, source, confidence, freshness, evidence, status, and budget-aware Context Pack selection. Legacy `.memory/*.md` data can be imported once with `scripts/migrate_legacy_memory.py`; product runtime must not read it directly.

### Legacy Runtime / CLI

There is no active CLI product path. Do not add new product work to CLI-style modules unless the CLI is explicitly revived.

The old root `api_server.py` shim has been removed. Keep compatibility-only behavior inside `src/api/legacy_runtime.py`; new runtime work should live in focused package modules such as `runtime_executor_service.py`.

### Backend API

The FastAPI backend is the primary integration layer for the frontend.

| Endpoint | Purpose |
|----------|---------|
| `POST /api/run` / `POST /api/runs` | Start workflow |
| `GET /api/run/{id}/events` / `GET /api/runs/{id}/events` | SSE event stream |
| `GET /api/files` | Workspace file tree |
| `GET /api/runs/{id}/state` | Run task board and runtime state |
| `GET /api/runs/{id}/context-pack` | Context pack for a run |
| `GET /api/runs/{id}/loop` | Agent loop state |
| `GET /api/config` | LLM provider status, system config |
| `GET/POST /api/memories` | Memory CRUD + search |

## Configuration

Config is in `.env` (gitignored), see `.env.example` for template.

**LLM providers** (auto-detection priority: Anthropic > DeepSeek > MiniMax > OpenAI > Ollama):

| Env var | Purpose |
|---------|---------|
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MINIMAX_API_KEY` | MiniMax |
| `OPENAI_API_KEY` | OpenAI |
| `OLLAMA_BASE_URL` | Ollama local (default `http://localhost:11434`) |
| `LLM_PROVIDER` | Explicit provider override |
| `LLM_TEMPERATURE` | Default 0.2 |
| `LLM_MAX_TOKENS` | Default 4096 |

Each provider also has a `*_MODEL` env var (e.g., `ANTHROPIC_MODEL`, `DEEPSEEK_MODEL`).

**Other config**: `LOG_LEVEL`, `LOG_FILE`, `CONTEXT_MAX_TOKENS`, `MAX_CODER_STEPS`, `MAX_PLANNER_STEPS`, `LLM_TIMEOUT_SECONDS`, `MAX_CONCURRENT_RUNS`, `SANDBOX_MEM_LIMIT`, `SANDBOX_TIMEOUT_SECONDS`.

## Database Safety

- Never run destructive database commands without explicit confirmation.
- Prefer local or staging database.
- Use readonly credentials for MCP database access.
- For migrations, inspect existing migrations first.
- Runtime state, events, task boards, and governed memory are persisted under each workspace.

## Git Rules

- Before large changes, inspect git status.
- Summarize changed files after edits.
- Do not commit unless explicitly asked.

## Runtime Data Directory

`PROJECT_ROOT` is the nanoCursor source directory. Do not use it as the implicit user project workspace.

By default, nanoCursor starts in an isolated workspace at `.nanocursor/workspaces/default`. Users can point startup to a real project with `NANOCURSOR_WORKSPACE_DIR`, or switch explicitly through the frontend/API. The framework writes project-level state into the currently opened workspace directory:

```
<opened-workspace>/
  .nanocursor/
    runs/{thread_id}/        # session.json, events.jsonl, report.md, requirements.json
    conversations/{id}/      # conversation.json, team.json
    skills/{name}/           # SKILL.md
    project_index.json
  .tasks/                    # JSON task persistence
  .team/                     # Teammate inboxes
  .backups/                  # File backups
  .snapshots/                # State snapshots
  .memory/                   # Persistent memories
```
