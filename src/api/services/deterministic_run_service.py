"""Shared lifecycle for deterministic demo and benchmark workers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.api.services.run_finalization_service import (
    finalize_delivery_best_effort,
    finalize_run_registry,
)
from src.api.services.runtime_registry_service import RuntimeRegistry, get_runtime_registry


def run_deterministic_worker(
    *,
    thread_id: str,
    workspace_dir: str,
    execute: Callable[[Callable[[str], None]], Any],
    error_title: str,
    error_payload: dict[str, Any] | None = None,
    registry: RuntimeRegistry | None = None,
) -> None:
    """Execute a deterministic event producer and always release its run registration."""
    runtime = registry or get_runtime_registry()
    final_status = "completed"

    def update_status(status: str) -> None:
        nonlocal final_status
        final_status = status
        with runtime.runs_lock:
            run_info = runtime.active_runs.get(thread_id)
            if run_info:
                run_info.set_status(status)

    try:
        execute(update_status)
    except Exception as exc:
        final_status = "failed"
        payload = dict(error_payload or {})
        payload["error"] = str(exc)
        runtime.event_store.append_event(
            thread_id=thread_id,
            event_type="error",
            title=error_title,
            content=str(exc),
            agent="lead",
            payload=payload,
            workspace_dir=workspace_dir,
        )
        runtime.event_store.update_session(thread_id, workspace_dir, status="failed", error=str(exc))
    finally:
        with runtime.runs_lock:
            run_info = runtime.active_runs.get(thread_id)
            if run_info:
                run_info.finalize_lifecycle(final_status)
                run_info.set_status(final_status)
                runtime.event_store.update_session(
                    thread_id,
                    workspace_dir,
                    status=final_status,
                    **run_info.session_metadata(),
                )
        finalize_delivery_best_effort(thread_id, workspace_dir)
        finalize_run_registry(
            active_runs=runtime.active_runs,
            runs_lock=runtime.runs_lock,
            run_manager=runtime.run_manager,
            thread_id=thread_id,
            final_status=final_status,
        )


__all__ = ["run_deterministic_worker"]
