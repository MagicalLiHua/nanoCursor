# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nanoCursor** is a multi-agent automatic programming framework that transforms user requests into working code through an LLM-driven agent loop with tool calling. It uses a simple while-loop architecture (inspired by s_full.py) rather than a complex state machine.

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

# Streamlit Web UI (alternative frontend)
streamlit run web_ui.py
```

Frontend development mode:
```bash
cd frontend
npm install    # only needed once
npm run dev    # starts Vite dev server on port 3000, proxies /api to localhost:8100
```

## Architecture

### Core Engine

The framework uses a simple agent loop pattern (inspired by s_full.py / s01_agent_loop.py):
- `src/core/engine.py` - 统一 MVP 引擎，整合所有功能模块

### Key Files

| File | Purpose |
|------|---------|
| `src/agent/engine.py` | Agent loop + 20 个工具处理函数 |
| `src/agent/state.py` | AgentState + WorkflowCancelledError |
| `src/agent/prompt.py` | 管道式系统提示构建 |
| `src/agent/error_recovery.py` | 错误恢复 + 指数退避 |
| `src/team/team.py` | 团队协作（MessageBus + TeammateManager） |
| `src/memory/manager.py` | 跨会话记忆（Markdown 持久化） |
| `src/memory/compactor.py` | 三层上下文压缩 |
| `src/tasks/manager.py` | TaskPool DAG 管理 |
| `src/tasks/skill.py` | 技能按需加载 |
| `src/infra/hooks.py` | 事件钩子系统 |
| `src/infra/background.py` | 后台任务管理 |
| `src/infra/cron.py` | 定时任务调度 |
| `src/infra/worktree.py` | Git worktree 隔离 |
| `src/infra/permission.py` | 权限管道 + Bash 安全验证 |
| `src/infra/db.py` | SQLite 持久化（todos/memories） |
| `src/infra/metrics.py` | MetricsCollector |
| `src/tools/file_tools.py` | 文件操作工具 |

### Tech Stack
- **Python 3.10+** with **asyncio** for async agent orchestration
- **LLM providers**: MiniMax (Anthropic-compatible), OpenAI, Anthropic, Ollama, DeepSeek
- **Pydantic v2** for structured output parsing
- **FastAPI + uvicorn** for backend API + SSE streaming
- **React 18 + TypeScript + Vite** for frontend
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

### Frontend Architecture

The React frontend uses a sidebar layout with four pages:
1. **工作台 (Chat)**: Chat interface + real-time SSE updates + execution log
2. **指标面板 (Metrics)**: LLM calls, tokens, latency, tool success rate
3. **文件浏览器 (Files)**: File tree + syntax-highlighted viewer
4. **配置面板 (Config)**: LLM provider status + system config + env vars

Global state managed via React Context (`AppContext.tsx`). SSE events drive real-time UI updates.

### Backend API (api_server.py)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/run` | Start workflow with prompt, return `{thread_id, status}` |
| `GET /api/run/{thread_id}/events` | SSE stream of events |
| `GET /api/run/{thread_id}/state` | Get final state |
| `GET /api/files` | Workspace file tree |
| `GET /api/files/{path}` | Read file content |
| `GET /api/metrics` | Metrics dump + historical |
| `GET /api/config` | LLM provider status, system config, env vars |
| `GET /api/snapshots` | List recovery snapshots |
| `GET /api/backups` | List backup files |
| `GET/POST /api/todos` | Todo CRUD |
| `GET/POST /api/memories` | Memory CRUD + search |

## Configuration

Config is in `src/core/.env` (gitignored), see `.env.example` for template. Supports `MINIMAX_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_BASE_URL`, `DEEPSEEK_API_KEY`.