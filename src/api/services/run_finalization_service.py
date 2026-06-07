"""Shared terminal run finalization helpers.

The legacy runtime still owns the main workflow loop, but terminal state should
be handled in one place so completed/cancelled/failed runs behave consistently.
"""

from __future__ import annotations

from typing import Any

from src.api.services import conversation_service
from src.api.services.run_lifecycle_service import finalize_run
from src.infra.logging import get_logger


TERMINAL_STATUSES = {"completed", "cancelled", "failed", "interrupted"}
logger = get_logger()


def _run_field(run_info: Any, key: str, default: Any = None) -> Any:
    if run_info is None:
        return default
    if hasattr(run_info, "get"):
        return run_info.get(key, default)
    return getattr(run_info, key, default)


def _log_best_effort_failure(
    operation: str,
    thread_id: str,
    workspace_dir: str | None,
    exc: Exception,
) -> None:
    logger.warning(
        f"best_effort_failed:{operation}",
        extra={"thread_id": thread_id, "path": workspace_dir or ""},
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def persist_terminal_session(
    *,
    event_store: Any,
    thread_id: str,
    workspace_dir: str,
    status: str,
    saved_messages: list[Any] | None = None,
    summary: str = "",
    execution_summary: str = "",
    error: str = "",
) -> dict[str, Any] | None:
    """Persist the terminal session fields without forcing empty values."""
    changes: dict[str, Any] = {"status": status}
    if summary:
        changes["summary"] = summary
    if execution_summary:
        changes["execution_summary"] = execution_summary
    if error:
        changes["error"] = error
    if saved_messages is not None:
        changes["saved_messages"] = saved_messages
    return event_store.update_session(thread_id, workspace_dir, **changes)


def finalize_conversation_for_run(
    *,
    thread_id: str,
    workspace_dir: str,
    status: str,
    active_runs: dict[str, Any],
    runs_lock: Any,
    event_store: Any,
    summary: str = "",
    error: str = "",
) -> str | None:
    """Sync a terminal run back to its owning conversation, when one exists."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        conversation_id = _run_field(run_info, "conversation_id")
    if not conversation_id:
        return None

    conversation_service.finalize_conversation_run(
        conversation_id=conversation_id,
        thread_id=thread_id,
        status=status,
        workspace_dir=workspace_dir,
        summary=summary,
        error=error,
    )
    event_store.update_session(
        thread_id,
        workspace_dir,
        conversation_id=conversation_id,
        conversation_status=status,
    )
    return str(conversation_id)


def finalize_agent_loop_best_effort(
    thread_id: str,
    workspace_dir: str,
    *,
    status: str,
    final_message: str = "",
) -> bool:
    """Finalize the agent-loop state, but never fail the workflow cleanup."""
    try:
        from src.api.services.agent_loop_state_service import finalize_agent_loop_state

        finalize_agent_loop_state(
            thread_id,
            workspace_dir,
            status=status,
            final_message=final_message,
        )
        return True
    except Exception as exc:
        _log_best_effort_failure("finalize_agent_loop", thread_id, workspace_dir, exc)
        return False


def finalize_delivery_best_effort(
    thread_id: str,
    workspace_dir: str | None,
) -> bool:
    """Generate the delivery contract without making it a hard dependency."""
    try:
        from src.api.services.delivery_service import finalize_delivery

        finalize_delivery(thread_id, workspace_dir)
        return True
    except Exception as exc:
        _log_best_effort_failure("finalize_delivery", thread_id, workspace_dir, exc)
        return False


def save_failures_best_effort(thread_id: str, workspace_dir: str | None) -> bool:
    """Classify and persist failures without masking the original failure."""
    try:
        from src.api.services.failure_classifier_service import save_failures

        save_failures(thread_id, workspace_dir)
        return True
    except Exception as exc:
        _log_best_effort_failure("save_failures", thread_id, workspace_dir, exc)
        return False


def extract_run_memory_best_effort(thread_id: str, workspace_dir: str) -> bool:
    """Extract evidence-backed run memory without making finalization brittle."""
    try:
        from src.api.services.memory_governance_service import extract_run_memory

        result = extract_run_memory(workspace_dir, thread_id)
        return bool(result.get("created") or result.get("reason"))
    except Exception as exc:
        _log_best_effort_failure("extract_run_memory", thread_id, workspace_dir, exc)
        return False


def finalize_run_registry(
    *,
    active_runs: dict[str, Any],
    runs_lock: Any,
    run_manager: Any,
    thread_id: str,
    final_status: str,
    error: str = "",
) -> dict[str, Any]:
    """Set the terminal in-memory status and release RunManager registration."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        if run_info:
            run_info.set_status(final_status)
    return finalize_run(run_manager, thread_id, final_status, error=error)


def complete_workflow_run(
    *,
    thread_id: str,
    workspace_dir: str,
    result: str,
    messages: list[Any],
    uses_runtime_turn_loop: bool,
    active_runs: dict[str, Any],
    runs_lock: Any,
    event_store: Any,
    emit_event: Any,
    sync_run_context: Any,
    emit_stage_updates: Any,
) -> None:
    """Emit and persist the successful terminal state for a workflow."""
    emit_event(
        thread_id=thread_id,
        event_type="assistant_message",
        title="Agent 回复",
        content=result[:5000],
        agent="lead",
        payload={"content": result},
        legacy_event={
            "type": "node_update",
            "node": "agent",
            "data": {"content": result[:1000]},
        },
        workspace_dir=workspace_dir,
    )
    with runs_lock:
        run_info = active_runs.get(thread_id)
        stage_updates = run_info.finalize_lifecycle("completed") if run_info else []
    sync_run_context(thread_id, workspace_dir)
    emit_stage_updates(thread_id, workspace_dir, stage_updates)
    emit_event(
        thread_id=thread_id,
        event_type="done",
        title="任务完成",
        content="Agent 运行已完成",
        agent="lead",
        payload={"status": "completed"},
        legacy_event={"type": "done", "status": "completed"},
        workspace_dir=workspace_dir,
    )
    if not uses_runtime_turn_loop:
        finalize_agent_loop_best_effort(
            thread_id,
            workspace_dir,
            status="completed",
            final_message=result,
        )
    persist_terminal_session(
        event_store=event_store,
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="completed",
        summary=result[:2000],
        execution_summary=result[:1200],
        saved_messages=messages,
    )
    extract_run_memory_best_effort(thread_id, workspace_dir)
    finalize_conversation_for_run(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="completed",
        active_runs=active_runs,
        runs_lock=runs_lock,
        event_store=event_store,
        summary=result,
    )


def cancel_workflow_run(
    *,
    thread_id: str,
    workspace_dir: str,
    messages: list[Any],
    active_runs: dict[str, Any],
    runs_lock: Any,
    event_store: Any,
    emit_event: Any,
    sync_run_context: Any,
    emit_stage_updates: Any,
) -> None:
    """Emit and persist the cancelled terminal state for a workflow."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        stage_updates = run_info.finalize_lifecycle("cancelled", "Agent 运行已取消") if run_info else []
    sync_run_context(thread_id, workspace_dir)
    emit_stage_updates(thread_id, workspace_dir, stage_updates)
    emit_event(
        thread_id=thread_id,
        event_type="done",
        title="任务已取消",
        content="Agent 运行已取消",
        agent="lead",
        payload={"status": "cancelled"},
        legacy_event={"type": "done", "status": "cancelled"},
        workspace_dir=workspace_dir,
    )
    finalize_agent_loop_best_effort(
        thread_id,
        workspace_dir,
        status="cancelled",
        final_message="Agent 运行已取消",
    )
    persist_terminal_session(
        event_store=event_store,
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="cancelled",
        summary="Agent 运行已取消",
        execution_summary="Agent 运行已取消",
        saved_messages=messages,
    )
    finalize_conversation_for_run(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="cancelled",
        active_runs=active_runs,
        runs_lock=runs_lock,
        event_store=event_store,
        summary="Agent 运行已取消",
    )


def fail_workflow_run(
    *,
    thread_id: str,
    workspace_dir: str,
    error: Exception,
    error_detail: str,
    messages: list[Any],
    active_runs: dict[str, Any],
    runs_lock: Any,
    event_store: Any,
    emit_event: Any,
    sync_run_context: Any,
    emit_stage_updates: Any,
) -> None:
    """Emit and persist the failed terminal state for a workflow."""
    error_text = str(error)
    with runs_lock:
        run_info = active_runs.get(thread_id)
        stage_updates = run_info.finalize_lifecycle("failed", error_text) if run_info else []
    sync_run_context(thread_id, workspace_dir)
    emit_stage_updates(thread_id, workspace_dir, stage_updates)
    emit_event(
        thread_id=thread_id,
        event_type="error",
        title="运行异常",
        content=error_text,
        agent="lead",
        payload={"error": error_text, "detail": error_detail},
        legacy_event={"type": "error", "message": error_text},
        workspace_dir=workspace_dir,
    )
    finalize_agent_loop_best_effort(
        thread_id,
        workspace_dir,
        status="failed",
        final_message=error_text,
    )
    persist_terminal_session(
        event_store=event_store,
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="failed",
        error=error_text,
        execution_summary=f"失败: {error_text[:1000]}",
        saved_messages=messages,
    )
    extract_run_memory_best_effort(thread_id, workspace_dir)
    save_failures_best_effort(thread_id, workspace_dir)
    finalize_conversation_for_run(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="failed",
        active_runs=active_runs,
        runs_lock=runs_lock,
        event_store=event_store,
        error=error_text,
    )
