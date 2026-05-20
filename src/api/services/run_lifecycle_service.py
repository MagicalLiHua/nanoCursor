"""Run lifecycle service — create, register, transition, finalize, recover, cleanup.

All run state transitions flow through this service so route handlers never
manipulate RunManager state machines directly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.runtime.run_state import RunStatus, RunMode
from src.runtime.run_manager import RunManager


def create_run_context(
    thread_id: str,
    workspace_dir: str,
    *,
    mode: RunMode = RunMode.NORMAL,
    conversation_id: str | None = None,
    team: list[dict[str, Any]] | None = None,
    execution_plan: dict[str, Any] | None = None,
    queue: Any = None,
) -> dict[str, Any]:
    """Build the dict needed to initialise a RunContext-compatible structure."""
    return {
        "thread_id": thread_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "status": RunStatus.CREATED.value,
        "mode": mode.value,
        "conversation_id": conversation_id,
        "team": list(team or []),
        "execution_plan": dict(execution_plan or {}),
        "queue": queue,
    }


def register_run(run_manager: RunManager, run_ctx: Any) -> str:
    """Register a run with RunManager. Returns thread_id on success, raises on conflict."""
    ok = run_manager.register(run_ctx)
    if not ok:
        raise RuntimeError("注册 run 失败")
    return run_ctx.thread_id


def transition_run(
    run_manager: RunManager,
    thread_id: str,
    new_status: str | RunStatus,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition a run to a new status. Returns the updated state dict."""
    sm = run_manager.transition(thread_id, new_status)
    return {
        "thread_id": thread_id,
        "status": sm.status.value if hasattr(sm.status, "value") else str(sm.status),
        "history": sm.history(),
        "reason": reason,
        "payload": payload or {},
    }


def finalize_run(
    run_manager: RunManager,
    thread_id: str,
    final_status: str | RunStatus,
    error: str = "",
) -> dict[str, Any]:
    """Record final run status and release lock."""
    run_manager.finalize(thread_id, final_status)
    run_manager.unregister(thread_id)
    return {
        "thread_id": thread_id,
        "final_status": final_status if isinstance(final_status, str) else final_status.value,
        "error": error,
    }


def recover_interrupted_runs(
    run_manager: RunManager,
    workspace_dir: str,
) -> list[str]:
    """Detect and mark interrupted runs. Returns list of recovered thread_ids."""
    return run_manager.detect_interrupted(workspace_dir)


def cleanup_stale_runs(
    run_manager: RunManager,
    workspace_dir: str,
    older_than_hours: int = 24,
) -> int:
    """Unregister any runs in RunManager that have been terminal for > older_than_hours.
    Returns count of cleaned runs."""
    workspace = Path(workspace_dir).resolve()
    runs_dir = workspace / ".nanocursor" / "runs"
    if not runs_dir.exists():
        return 0

    cutoff = time.time() - older_than_hours * 3600
    cleaned = 0

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        session_file = run_dir / "session.json"
        if not session_file.exists():
            continue
        try:
            session = json.loads(session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        thread_id = session.get("thread_id", run_dir.name)
        status = session.get("status", "")
        completed_at = session.get("completed_at", 0) or session.get("updated_at", 0) or 0

        if status not in ("completed", "cancelled", "failed", "interrupted"):
            continue
        if completed_at > cutoff:
            continue

        # If still in active registry, finalize and unregister
        sm = run_manager.get_state_machine(thread_id)
        if sm and sm.is_terminal():
            run_manager.unregister(thread_id)
            cleaned += 1

    return cleaned
