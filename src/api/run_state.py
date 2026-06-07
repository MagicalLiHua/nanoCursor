"""Shared run state singleton for API routes.

Centralizes RunManager, EventStore, and helper functions that were previously
scattered as module-level globals in legacy runtime.
"""

from __future__ import annotations

import time as _time
import uuid
from pathlib import Path
from typing import Any

from src.api.models import AgentEvent
from src.api.services.runtime_registry_service import get_runtime_registry
from src.infra.logging import get_logger
from src.runtime.audit_log import AuditRecord, get_audit_repo
from src.runtime.run_state import RunStatus


# --- Singletons ---
logger = get_logger()
_registry = get_runtime_registry()
run_manager = _registry.run_manager
event_store = _registry.event_store
active_runs = _registry.active_runs
runs_lock = _registry.runs_lock


def get_workspace() -> str:
    from src.api.services.workspace_runtime_service import get_active_workspace

    return get_active_workspace()


def set_active_workspace(workspace_dir: str) -> str:
    from src.api.services.workspace_runtime_service import set_active_workspace as _set

    return _set(workspace_dir)


def workspace_for_thread(thread_id: str) -> str:
    """Resolve workspace dir for a thread from active_runs, event_store, or recent projects."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        workspace_dir = run_info.get("workspace_dir") if run_info else None
    if workspace_dir:
        return workspace_dir

    store = event_store
    if store is not None:
        try:
            indexed_workspace = store.workspace_for_thread(thread_id)
            if indexed_workspace and store.get_session(thread_id, indexed_workspace):
                return indexed_workspace

            session = store.get_session(thread_id)
            if session and session.get("workspace_dir"):
                return str(Path(session["workspace_dir"]).resolve())

            from src.api.services.workspace_registry_service import list_recent_projects
            for item in list_recent_projects():
                candidate = item.get("path") if isinstance(item, dict) else None
                if candidate and store.get_session(thread_id, candidate):
                    return str(Path(candidate).resolve())
        except Exception as exc:
            logger.warning(
                "workspace_resolution_fallback",
                extra={"thread_id": thread_id},
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    return get_workspace()


def session_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Resolve a run session from active_runs, event_store index, or recent projects."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        workspace_dir = run_info.get("workspace_dir") if run_info else None
    if workspace_dir:
        session = event_store.get_session(thread_id, workspace_dir)
        if session:
            return session

    try:
        indexed_workspace = event_store.workspace_for_thread(thread_id)
    except Exception as exc:
        logger.debug(
            "session_resolution_index_failed",
            extra={"thread_id": thread_id},
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        indexed_workspace = None
    if indexed_workspace:
        session = event_store.get_session(thread_id, indexed_workspace)
        if session:
            return session

    try:
        current_workspace = str(Path(get_workspace()).resolve())
        session = event_store.get_session(thread_id, current_workspace)
        if session:
            return session
    except Exception as exc:
        logger.debug(
            "session_resolution_active_workspace_failed",
            extra={"thread_id": thread_id},
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    try:
        from src.api.services.workspace_registry_service import list_recent_projects
        for item in list_recent_projects():
            candidate = item.get("path") if isinstance(item, dict) else None
            if not candidate:
                continue
            session = event_store.get_session(thread_id, candidate)
            if session:
                return session
    except Exception as exc:
        logger.warning(
            "session_resolution_recent_projects_failed",
            extra={"thread_id": thread_id},
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    return None


def should_cancel_run(thread_id: str) -> bool:
    sm = run_manager.get_state_machine(thread_id)
    if sm and sm.status in {
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
    }:
        return True
    with runs_lock:
        run_info = active_runs.get(thread_id)
        return bool(run_info and run_info.get("status") in {"cancelling", "cancelled", "failed", "interrupted"})


def emit_agenthub_event(
    thread_id: str,
    event_type: str,
    title: str = "",
    content: str = "",
    agent: str = "lead",
    payload: dict[str, Any] | None = None,
    legacy_event: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
) -> AgentEvent:
    """Persist a unified event, mirror task-board state, and optionally enqueue legacy SSE."""
    if workspace_dir is None:
        with runs_lock:
            run_info = active_runs.get(thread_id)
            workspace_dir = run_info.get("workspace_dir") if run_info else None
    workspace_dir = workspace_dir or get_workspace()
    event = event_store.append_event(
        thread_id=thread_id,
        event_type=event_type,
        title=title,
        content=content,
        agent=agent,
        payload=payload or {},
        workspace_dir=workspace_dir,
    )
    if event_type in {
        "task_created",
        "task_updated",
        "stage_updated",
        "tool_call_finished",
        "file_changed",
        "diff_updated",
        "test_finished",
        "report_ready",
        "done",
        "assistant_message",
        "quality_gate",
        "delivery_scored",
        "agent_run_started",
        "agent_result_merged",
        "agent_run_failed",
        "parallel_agent_progress",
        "parallel_agent_result",
        "parallel_agent_failed",
        "parallel_agents_completed",
    }:
        try:
            from src.api.services.run_state_service import mirror_domain_event_to_task_board

            mirror_domain_event_to_task_board(
                thread_id=thread_id,
                workspace_dir=workspace_dir,
                event_type=event_type,
                payload=payload or {},
                title=title,
                content=content,
                agent=agent,
                event_id=event.id,
                timestamp=event.timestamp,
            )
        except Exception as exc:
            logger.warning(
                "task_board_mirror_failed",
                extra={"thread_id": thread_id, "path": workspace_dir},
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    if legacy_event is not None:
        import json

        with runs_lock:
            run_info = active_runs.get(thread_id)
            event_queue = run_info.get("queue") if run_info else None
        if event_queue:
            enriched = dict(legacy_event)
            enriched["agenthub_event"] = event.model_dump()
            event_queue.put(json.dumps(enriched, ensure_ascii=False))

    return event


def emit_agent_activity(
    *,
    thread_id: str,
    agent: str = "lead",
    title: str,
    content: str = "",
    workspace_dir: str | None = None,
    payload: dict[str, Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> AgentEvent:
    """Emit a user-facing progress heartbeat."""
    merged = dict(payload or {})
    if input_tokens or output_tokens:
        merged["input_tokens"] = input_tokens
        merged["output_tokens"] = output_tokens
    return emit_agenthub_event(
        thread_id=thread_id,
        event_type="agent_activity",
        title=title,
        content=content,
        agent=agent,
        payload=merged,
        workspace_dir=workspace_dir,
    )


def sync_run_context(thread_id: str, workspace_dir: str) -> Any:
    """Persist the current in-memory run context into the session file."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        metadata = run_info.session_metadata() if run_info else None
    if not run_info or not metadata:
        return run_info
    event_store.update_session(thread_id, workspace_dir, **metadata)
    try:
        from src.api.services.run_ledger_service import sync_steps_from_lifecycle
        sync_steps_from_lifecycle(thread_id, metadata, workspace_dir)
    except Exception:
        logger.warning(
            "run_ledger_sync_failed",
            extra={"thread_id": thread_id, "workspace_id": workspace_dir},
            exc_info=True,
        )
    return run_info


def transition_runtime_state(thread_id: str, workspace_dir: str, status: RunStatus) -> None:
    """Best-effort sync between RunManager state and the durable run session."""
    sm = run_manager.get_state_machine(thread_id)
    if sm and sm.status != status and sm.can_transition(status):
        run_manager.transition(thread_id, status)

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if run_info:
            run_info.set_status(status.value)
    sync_run_context(thread_id, workspace_dir)


def emit_stage_updates(
    thread_id: str,
    workspace_dir: str,
    updates: list[dict[str, Any]] | None,
) -> None:
    for update in updates or []:
        emit_agenthub_event(
            thread_id=thread_id,
            event_type="stage_updated",
            title=f"阶段状态：{update.get('title') or update.get('stage_id')}",
            content=f"{update.get('previous_status')} -> {update.get('status')}",
            agent=str(update.get("owner") or "lead").lower(),
            payload=update,
            workspace_dir=workspace_dir,
        )


def audit_route_action(
    *,
    thread_id: str,
    workspace_dir: str,
    kind: str,
    target: str = "",
    decision: str = "",
    result: str = "",
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit for route-level actions outside the action pipeline."""
    try:
        get_audit_repo().append(
            AuditRecord(
                audit_id=f"audit_{uuid.uuid4().hex[:12]}",
                thread_id=thread_id,
                action_id=f"route_{uuid.uuid4().hex[:12]}",
                kind=kind,
                target=target,
                decision=decision,
                result=result,
                reason=reason,
                detail=detail or {},
                created_at=_time.time(),
            ),
            workspace_dir,
        )
    except Exception:
        logger.warning(
            "route_audit_persist_failed",
            extra={"thread_id": thread_id, "workspace_id": workspace_dir},
            exc_info=True,
        )
