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

# Run Streamlit Web UI (recommended)
streamlit run web_ui.py

# Run CLI (executes hardcoded prompt in run.py)
python run.py
```

Docker Desktop must be running for sandbox functionality.

## Architecture

### Tech Stack
- **Python 3.10+** with **LangGraph** (~1.0.x) for agent orchestration via `StateGraph`
- **LLM providers**: OpenAI, Anthropic, Ollama, DeepSeek (configured via `.env`)
- **Docker SDK** for sandbox isolation (`python:3.10-slim`, no network, 256MB memory limit)
- **Pydantic v2** for structured output parsing
- **Streamlit** for the Web UI
- **python-dotenv** for config; config lives in `src/core/.env` (gitignored)

### Key Files

| File | Purpose |
|------|---------|
| `run.py` | LangGraph `StateGraph` builder & CLI entry point |
| `web_ui.py` | Streamlit UI with real-time node streaming via `stream_mode="updates"` |
| `src/agents/Planner.py` | Planner node: explores workspace, generates development plan |
| `src/agents/Coder.py` | Coder node: reads/edits/writes files |
| `src/agents/Sandbox.py` | Sandbox node: Docker container execution |
| `src/agents/Reviewer.py` | Reviewer node: analyzes sandbox error output |
| `src/core/state.py` | `AgentState` TypedDict -- shared blackboard across all nodes |
| `src/core/llm_engine.py` | Multi-provider LLM init + retry wrapper with exponential backoff |
| `src/core/context_manager.py` | v2.0 layered context management (core/working/reference memory) |
| `src/core/config.py` | Path resolution (`WORKSPACE_DIR`), env bootstrap |
| `src/core/logger.py` | Structured logger factory |
| `src/core/repo_map.py` | AST-based function/class signature extraction |
| `src/core/metrics.py` | Thread-safe `MetricsCollector` singleton tracking LLM calls/tokens/latency, tool success rate, repair cycle outcomes; outputs to `workspace/metrics.json` |
| `src/core/recovery.py` | Workspace snapshot system: on circuit breaker, captures all `.py` files, active_files content, modification log, and conversation summary into `workspace/.snapshots/` |
| `src/tools/file_tools.py` | 8 file tools: read, write, edit (fuzzy match), read_function, read_class, read_file_range, backup/rollback |

### Workflow Graph (from `run.py`)

The LangGraph `StateGraph` has conditional routing:

- **route_after_planner**: Tool calls -> `planner_tools` (runs `planner_tools`: `read_file`, `list_directory`) -> back to Planner. No tool calls -> Coder.
- **route_after_coder**: Tool calls -> increment `coder_step_counter` -> `coder_tools` (full tool set) -> back to Coder. No tool calls or `coder_step_count >= max_coder_steps` (default 15) -> Sandbox.
- **route_after_sandbox**: No error -> END. Error + retry < max -> Reviewer -> Coder (repair loop). Retry >= max -> `create_workspace_snapshot` via recovery.py -> END (circuit breaker).

Each `record_metrics` node logs tool success/failure before proceeding.

### Context Management (v2.0)

`context_manager.py` implements a three-layer strategy:
1. **Core memory**: User request, plan, error traces (permanent)
2. **Working memory**: Recent N turns (sliding window, dynamically adjustable)
3. **Reference memory**: LLM-driven structured summary of older history

Uses `tiktoken` (`cl100k_base`) for precise token counting, with dynamic window sizing based on usage ratio.

### File Operations

`file_tools.py` provides 8 tools with three-tier edit matching: exact match -> stripped match -> fuzzy `difflib` match at 90% threshold. Large files (>5000 chars) are read via AST outline. All paths are validated against traversal attacks.

## Configuration

Config is in `src/core/.env` (gitignored), see `.env.example` for template. Supports `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, `DEEPSEEK_API_KEY` for different LLM providers.
