"""Legacy runtime compatibility module for the nanoCursor backend.

New runtime commands should start ``src.api.server:app``. This module only keeps
workflow compatibility wrappers, historical monkeypatch exports, and production
static serving while the remaining compatibility surface is retired.
"""

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Ensure project root is in sys.path. This file lives in ``src/api``.
ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Create compatibility app via the same factory and lifecycle as the official entrypoint.
from src.api.app import create_app
app = create_app()

from src.agent.engine import TOOLS, agent_loop, agent_loop_stream, get_workdir, run_subagent
import src.infra.config as config_module
from src.infra.metrics import metrics as metrics_collector

from src.api.run_state import (
    emit_agent_activity as _emit_agent_activity,
    emit_agenthub_event as _emit_agenthub_event,
    emit_stage_updates as _emit_stage_updates,
    get_workspace as _get_workspace,
    session_for_thread as _session_for_thread,
    set_active_workspace as _set_active_workspace,
    sync_run_context as _sync_run_context,
    transition_runtime_state as _transition_runtime_state,
    workspace_for_thread as _workspace_for_thread,
)
from src.api.services.runtime_executor_service import (
    RuntimeExecutorDependencies,
    cancel_agent_pool as _executor_cancel_agent_pool,
    is_simple_lead_message as _executor_is_simple_lead_message,
    run_readonly_subagent as _executor_run_readonly_subagent,
    run_workflow as _executor_run_workflow,
    run_workflow_async as _executor_run_workflow_async,
    run_workflow_async_from_messages as _executor_run_workflow_async_from_messages,
    run_workflow_from_messages as _executor_run_workflow_from_messages,
    should_cancel_run as _executor_should_cancel_run,
)

from src.api.services.parallel_agent_service import run_parallel_agent_briefing
from src.api.services.runtime_registry_service import get_runtime_registry
from src.api.services.runtime_lifecycle_service import (
    active_runs_state_path,
    recover_interrupted_runs as _recover_interrupted_runs_impl,
    runtime_lifespan as lifespan,
    save_active_runs_state as _save_active_runs_state_impl,
)

# Persistent metrics history file (project root, preserved across workspaces)
METRICS_HISTORY_FILE = os.path.join(ROOT, "metrics_history.json")

# ============================================================
# Active run management
# ============================================================

_runtime_registry = get_runtime_registry()
run_manager = _runtime_registry.run_manager
active_runs = _runtime_registry.active_runs
runs_lock = _runtime_registry.runs_lock
event_store = _runtime_registry.event_store
ACTIVE_RUNS_STATE_FILE = str(active_runs_state_path())


def _save_active_runs_state():
    return _save_active_runs_state_impl(_runtime_registry)


def _recover_interrupted_runs():
    return _recover_interrupted_runs_impl(_runtime_registry, workspace_dir=_get_workspace())


def _executor_dependencies() -> RuntimeExecutorDependencies:
    return RuntimeExecutorDependencies(
        agent_loop_stream=agent_loop_stream,
        run_subagent=run_subagent,
        tools=TOOLS,
        get_workdir=get_workdir,
        metrics_collector=metrics_collector,
        metrics_history_file=METRICS_HISTORY_FILE,
        run_parallel_agent_briefing=run_parallel_agent_briefing,
        run_manager=run_manager,
        active_runs=active_runs,
        runs_lock=runs_lock,
        event_store=event_store,
        emit_event=_emit_agenthub_event,
        emit_activity=_emit_agent_activity,
        emit_stage_updates=_emit_stage_updates,
        transition_state=_transition_runtime_state,
        sync_run_context=_sync_run_context,
        get_workspace=_get_workspace,
    )


def _should_cancel_run(thread_id: str) -> bool:
    return _executor_should_cancel_run(thread_id, _executor_dependencies())


def _cancel_agent_pool(thread_id: str):
    return _executor_cancel_agent_pool(thread_id)


def _is_simple_lead_message(prompt: str) -> bool:
    return _executor_is_simple_lead_message(prompt)


async def _run_readonly_subagent(
    prompt: str,
    system: str,
    agent_type: str,
    tools: list[dict[str, Any]] | None = None,
) -> str:
    return await _executor_run_readonly_subagent(
        prompt=prompt,
        system=system,
        agent_type=agent_type,
        tools=tools,
        dependencies=_executor_dependencies(),
    )


def _run_workflow(thread_id: str, initial_messages: list, workspace_dir: str, max_retries: int = 3, max_coder_steps: int = 15):
    return _executor_run_workflow(
        thread_id,
        initial_messages,
        workspace_dir,
        max_retries=max_retries,
        max_coder_steps=max_coder_steps,
        dependencies=_executor_dependencies(),
    )


def _run_workflow_from_messages(thread_id: str, messages: list, system: str, workspace_dir: str):
    """Resume a run with pre-built messages and system prompt."""
    return _executor_run_workflow_from_messages(
        thread_id,
        messages,
        system,
        workspace_dir,
        dependencies=_executor_dependencies(),
    )


async def _run_workflow_async(thread_id: str, initial_messages: list, max_retries: int, max_coder_steps: int, workspace_dir: str | None = None):
    """Async internal implementation of _run_workflow."""
    return await _executor_run_workflow_async(
        thread_id,
        initial_messages,
        max_retries,
        max_coder_steps,
        workspace_dir,
        dependencies=_executor_dependencies(),
    )


async def _run_workflow_async_from_messages(thread_id: str, messages: list, system: str, workspace_dir: str):
    """Resume a run with pre-built messages and system prompt. Simplified version of _run_workflow_async."""
    return await _executor_run_workflow_async_from_messages(
        thread_id,
        messages,
        system,
        workspace_dir,
        dependencies=_executor_dependencies(),
    )


# ============================================================
# Static file serving (production)
# ============================================================

def serve_frontend(production: bool = False):
    if not production:
        return

    dist_dir = os.path.join(ROOT, "frontend", "dist")

    if os.path.exists(dist_dir):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_index(full_path: str):
            index_path = os.path.join(dist_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend 未构建")


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  nanoCursor API Server")
    print("=" * 60)
    print(f"  工作区: {config_module.WORKSPACE_DIR}")
    print("  开发模式: 运行 'cd frontend && npm run dev'")
    print("  生产模式: 先 'npm run build'，再运行此脚本")
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8100)
