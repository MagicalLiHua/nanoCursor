# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nanoCursor** is a multi-agent automatic programming framework that transforms user requests into working code through an LLM-driven agent loop with tool calling. It uses a simple while-loop architecture (inspired by s_full.py) rather than a complex state machine.

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

# Start interactive CLI (main entry point)
python cli.py

# One-shot mode: run a single prompt
python cli.py "帮我找 bug"

# Start web backend
python -m uvicorn api_server:app --host 127.0.0.1 --port 8100

# Start web frontend (separate terminal)
cd frontend && npm run dev

# Run old CLI (executes hardcoded prompt)
python run.py
```

## Architecture

### Core Engine

The framework uses a simple agent loop pattern (inspired by s_full.py / s01_agent_loop.py):
- `src/agent/engine.py` - Agent loop + 20 个工具处理函数

### Key Files

| File | Purpose |
|------|---------|
| `cli.py` | CLI 入口（交互式 REPL + 单次模式） |
| `api_server.py` | FastAPI 后端，Web 工作台 API |
| `src/cli/repl.py` | REPL 循环 + prompt_toolkit 输入 |
| `src/cli/renderer.py` | Rich 终端渲染（Markdown/代码/表格/流式） |
| `src/cli/commands.py` | 斜杠命令系统（/help /files /team /memory /task 等） |
| `src/agent/engine.py` | Agent loop + 20 个工具处理函数 |
| `src/agent/state.py` | AgentState + WorkflowCancelledError |
| `src/agent/prompt.py` | 管道式系统提示构建 |
| `src/agent/error_recovery.py` | 错误恢复 + 指数退避 |
| `src/agent/learner.py` | Agent 学习器（从运行中学习） |
| `src/api/models.py` | API Pydantic 数据模型 |
| `src/api/services/` | 业务服务层（会话、运行、蓝图、质量、报告等 20 个服务） |
| `src/indexer/indexer.py` | 项目索引器 |
| `src/team/team.py` | 团队协作（MessageBus + TeammateManager） |
| `src/memory/manager.py` | 跨会话记忆（Markdown 持久化） |
| `src/memory/compactor.py` | 三层上下文压缩 |
| `src/tasks/manager.py` | TaskPool DAG 管理 |
| `src/tasks/skill.py` | 技能按需加载 |
| `src/tools/file_tools.py` | 文件操作 |
| `src/tools/bash_tools.py` | Bash 命令执行 |
| `src/tools/git_tools.py` | Git 操作 |
| `src/tools/memory_tools.py` | 记忆 CRUD 工具 |
| `src/tools/project_tools.py` | 项目级工具 |
| `src/tools/todo_tools.py` | Todo 管理工具 |
| `src/infra/config.py` | 配置管理 |
| `src/infra/llm_config.py` | LLM 提供商配置 |
| `src/infra/hooks.py` | 事件钩子系统 |
| `src/infra/background.py` | 后台任务管理 |
| `src/infra/cron.py` | 定时任务调度 |
| `src/infra/worktree.py` | Git worktree 隔离 |
| `src/infra/permission.py` | 权限管道 + Bash 安全验证 |
| `src/infra/db.py` | SQLite 持久化（todos/memories） |
| `src/infra/metrics.py` | MetricsCollector |
| `src/infra/schemas.py` | 共享 Pydantic schemas |
| `src/infra/messages.py` | 消息流管理 |

### Tech Stack
- **Python 3.10+** with **asyncio** for async agent orchestration
- **LLM providers**: DeepSeek, MiniMax (Anthropic-compatible), OpenAI, Anthropic, Ollama
- **Pydantic v2** for structured output parsing
- **Rich + prompt_toolkit** for CLI interface
- **FastAPI + uvicorn** for optional backend API
- **python-dotenv** for config

### Core Concepts

**Agent Loop**: The core engine (`agent_loop()`) is a simple while loop:
1. Send messages to LLM with tool definitions
2. If `stop_reason == "tool_use"`, process tool calls and continue
3. Otherwise return the final text response

**Tool Calling**: Tools are defined in Anthropic `input_schema` format. Agent calls tools via `client.messages.create()`.

**Subagents**: `run_subagent()` spawns independent agent contexts for isolated tasks.

**Team System**: Persistent autonomous teammates with JSONL inbox communication:
- `spawn_teammate`: Launch a named teammate with role and system prompt
- `list_teammates`, `send_message`, `read_inbox`, `broadcast`: Team communication
- `shutdown_request`, `shutdown_response`: Graceful shutdown protocol
- `plan_approval`: Plan review workflow with RequestStore
- Teammates auto-poll for tasks and claim unclaimed work

**Task System**: JSON file-based task persistence in `.tasks/` directory with DAG dependency support.

**Memory System**: Markdown file-based persistent memories organized by category (user/feedback/project/reference).

### CLI Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/clear` | Start a new conversation thread |
| `/files` (or `/ls`) | List workspace files |
| `/cat <path>` | Display file contents with syntax highlighting |
| `/config` | Show LLM configuration |
| `/metrics` | Show current metrics (LLM calls, tokens, tool success) |
| `/workspace` (or `/pwd`) | Show current workspace path |
| `/model <name>` | Hot-switch LLM model for current session |
| `/team spawn/list/send/inbox/shutdown` | Multi-agent team management |
| `/memory save/search/list` | Persistent memory management |
| `/task create/list/update/graph` | Task DAG management |

### Backend API (api_server.py, optional)

The FastAPI backend is retained for optional external integration but is not the primary interface.

| Endpoint | Purpose |
|----------|---------|
| `POST /api/run` | Start workflow |
| `GET /api/run/{id}/events` | SSE event stream |
| `GET /api/files` | Workspace file tree |
| `GET /api/metrics` | Metrics dump + historical |
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
- This project uses SQLite locally (via `src/infra/db.py`). No production database in repo.

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
