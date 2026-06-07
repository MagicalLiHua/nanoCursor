"""Mutable run-state persistence, context binding, and lightweight scheduling."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.api.services.context_service import build_context_pack
from src.api.services.event_store import get_event_store
from src.runtime.task_board import (
    RunTask,
    RunTaskBoard,
    build_task_board,
    load_task_board,
    save_task_board,
)
from src.tools.tool_result import is_tool_error_output


TASK_EVENT_STATUS_MAP = {
    "todo": "pending",
    "pending": "pending",
    "ready": "ready",
    "in_progress": "running",
    "running": "running",
    "doing": "running",
    "blocked": "blocked",
    "completed": "passed",
    "complete": "passed",
    "done": "passed",
    "passed": "passed",
    "failed": "failed",
    "error": "failed",
    "skipped": "skipped",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

EVIDENCE_EVENT_TYPES = {
    "tool_call_finished",
    "file_changed",
    "diff_updated",
    "test_finished",
    "report_ready",
    "done",
    "assistant_message",
    "quality_gate",
    "delivery_scored",
    "ephemeral_agent_spawned",
    "ephemeral_agent_updated",
    "ephemeral_agent_completed",
    "ephemeral_agent_archived",
    "ephemeral_agent_expired",
    "agent_run_started",
    "agent_result_merged",
    "agent_run_failed",
    "parallel_agent_progress",
    "parallel_agent_result",
    "parallel_agent_failed",
    "parallel_agents_completed",
}


def _run_dir(thread_id: str, workspace_dir: str) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir)


def get_or_create_run_state(thread_id: str, workspace_dir: str) -> RunTaskBoard:
    """Load a persisted mutable task board or build one from the run session."""
    run_dir = _run_dir(thread_id, workspace_dir)
    board = load_task_board(run_dir)
    if board:
        board = _normalize_task_board(board)
        board.ready_nodes()
        save_task_board(board, run_dir)
        return board

    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir) or {}
    board = build_task_board(
        thread_id,
        execution_plan=session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {},
        conversation_id=session.get("conversation_id"),
    )
    board.ready_nodes()
    save_task_board(board, run_dir)
    store.append_event(
        thread_id,
        "run_state_created",
        title="运行状态地图已创建",
        content=f"Mutable task board created with {len(board.nodes)} tasks.",
        agent="lead",
        payload={"task_count": len(board.nodes), "node_count": len(board.nodes), "strategy": board.strategy},
        workspace_dir=workspace_dir,
    )
    return board


def get_run_task_board(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Return the primary Agent Loop friendly task-board representation."""
    board = get_or_create_run_state(thread_id, workspace_dir)
    return board.to_task_board()


def get_run_tasks_readonly(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Return run-scoped tasks without creating or mutating run-state files.

    This is the preferred read path for frontends and smoke checks. The older
    ``/state`` endpoints intentionally create a mutable task board from the
    execution plan when none exists. This read-only view avoids that side
    effect so an empty conversation or lightweight direct reply cannot grow
    phantom tasks merely because the UI refreshed.
    """
    store = get_event_store()
    run_dir = _run_dir(thread_id, workspace_dir)
    session = store.get_session(thread_id, workspace_dir) or {}
    execution_plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    if execution_plan.get("strategy") == "lead_direct_reply":
        return {
            "thread_id": thread_id,
            "run_id": thread_id,
            "conversation_id": session.get("conversation_id"),
            "strategy": "lead_direct_reply",
            "status": session.get("status") or "unknown",
            "tasks": [],
            "total": 0,
            "persisted": bool(load_task_board(run_dir)),
            "source": "lead_direct_reply",
        }

    board = load_task_board(run_dir)
    if board:
        data = board.to_task_board()
        data.update({"thread_id": thread_id, "persisted": True, "source": "run_state"})
        data["total"] = len(data.get("tasks", []))
        return data

    if execution_plan:
        board = build_task_board(
            thread_id,
            execution_plan=execution_plan,
            conversation_id=session.get("conversation_id"),
        )
        board.ready_nodes()
        data = board.to_task_board()
        data.update({"thread_id": thread_id, "persisted": False, "source": "execution_plan_derived"})
        data["total"] = len(data.get("tasks", []))
        return data

    return {
        "thread_id": thread_id,
        "run_id": thread_id,
        "conversation_id": session.get("conversation_id"),
        "strategy": "",
        "status": session.get("status") or "unknown",
        "tasks": [],
        "total": 0,
        "persisted": False,
        "source": "missing",
    }


def patch_run_state(
    thread_id: str,
    workspace_dir: str,
    patch: dict[str, Any],
) -> RunTaskBoard:
    """Apply a mutable run-state patch.

    This is the main API for the Lead loop to revise its task map while it
    observes tool results. It intentionally avoids treating the map as a fixed
    workflow.
    """
    board = get_or_create_run_state(thread_id, workspace_dir)
    reason = str(patch.get("reason") or "agent_loop_update")

    task_patches = list(patch.get("add_or_update_tasks", []) or [])
    task_patches.extend(patch.get("add_or_update_nodes", []) or [])
    for item in task_patches:
        if not isinstance(item, dict):
            continue
        try:
            node = RunTask(
                id=str(item.get("id")),
                type=str(item.get("type") or "analysis"),  # type: ignore[arg-type]
                title=str(item.get("title") or item.get("id")),
                goal=str(item.get("goal") or ""),
                agent_role=str(item.get("agent_role") or "lead"),
                dependencies=[str(dep) for dep in item.get("dependencies", []) if str(dep).strip()],
                can_parallel=bool(item.get("can_parallel", False)),
                writes_files=bool(item.get("writes_files", False)),
                resource_locks=[str(lock) for lock in item.get("resource_locks", []) if str(lock).strip()],
                tool_policy=item.get("tool_policy") if isinstance(item.get("tool_policy"), dict) else {},
                context_policy=item.get("context_policy") if isinstance(item.get("context_policy"), dict) else {},
            )
        except ValidationError as exc:
            raise ValueError(f"Invalid task patch: {exc.errors()}") from exc
        board.add_or_update_task(node, reason=reason)

    for item in list(patch.get("remove_tasks", []) or []) + list(patch.get("remove_nodes", []) or []):
        board.remove_task(str(item), reason=reason)

    for item in patch.get("connect", []) or []:
        if isinstance(item, dict):
            upstream = item.get("upstream_task") or item.get("from_task") or item.get("from_node")
            downstream = item.get("downstream_task") or item.get("to_task") or item.get("to_node")
            board.connect_tasks(str(upstream), str(downstream), reason=reason)

    for item in patch.get("disconnect", []) or []:
        if isinstance(item, dict):
            upstream = item.get("upstream_task") or item.get("from_task") or item.get("from_node")
            downstream = item.get("downstream_task") or item.get("to_task") or item.get("to_node")
            board.disconnect_tasks(str(upstream), str(downstream), reason=reason)

    metadata = patch.get("metadata")
    if isinstance(metadata, dict) and metadata:
        board.metadata.update(metadata)
        board.record_change("metadata_updated", {"keys": sorted(metadata.keys()), "reason": reason})

    board.ready_nodes()
    save_task_board(board, _run_dir(thread_id, workspace_dir))
    get_event_store().append_event(
        thread_id,
        "run_state_patched",
        title="运行状态已更新",
        content=reason,
        agent="lead",
        payload={
            "revision": board.revision,
            "task_count": len(board.nodes),
            "node_count": len(board.nodes),
            "reason": reason,
        },
        workspace_dir=workspace_dir,
    )
    return board


def mirror_domain_event_to_task_board(
    thread_id: str,
    workspace_dir: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    title: str = "",
    content: str = "",
    agent: str = "lead",
    event_id: str = "",
    timestamp: float | None = None,
) -> bool:
    """Mirror task-oriented domain events into the mutable task board.

    Tool calls such as ``task_create`` and ``task_update`` already produce
    user-facing events. This bridge makes those events operational too, so the
    Agent Loop's external task board stays aligned with what actually happened.
    """
    data = payload if isinstance(payload, dict) else {}
    event_ts = timestamp or time.time()
    if event_type == "task_created":
        task_data = data.get("task") if isinstance(data.get("task"), dict) else {}
        task_id = str(data.get("task_id") or task_data.get("id") or "").strip()
        if not task_id:
            return False
        status = _task_status_from_event(task_data.get("status") or data.get("status") or "pending")
        board = get_or_create_run_state(thread_id, workspace_dir)
        board.add_or_update_task(
            RunTask(
                id=task_id,
                type=_task_type_from_event(task_data, agent),
                title=str(task_data.get("title") or title or task_id),
                goal=str(task_data.get("description") or task_data.get("goal") or content or ""),
                agent_role=_agent_role_from_event(task_data.get("owner") or task_data.get("agent_role") or agent),
                status=status,
                dependencies=[
                    str(dep)
                    for dep in (task_data.get("dependencies") or task_data.get("blocked_by") or [])
                    if str(dep).strip()
                ],
                can_parallel=bool(task_data.get("can_parallel", False)),
                writes_files=bool(task_data.get("writes_files", False)),
                context_policy={"mode": "event", "source": "task_created"},
            ),
            reason="domain_event_task_created",
        )
        board.ready_nodes()
        save_task_board(board, _run_dir(thread_id, workspace_dir))
        return True

    if event_type == "task_updated":
        task_id = str(data.get("task_id") or "").strip()
        if not task_id:
            return False
        board = get_or_create_run_state(thread_id, workspace_dir)
        task = board.task(task_id)
        if not task:
            board.add_or_update_task(
                RunTask(
                    id=task_id,
                    type="analysis",
                    title=str(title or task_id),
                    goal=str(content or ""),
                    agent_role=_agent_role_from_event(agent),
                    context_policy={"mode": "event", "source": "task_updated"},
                ),
                reason="domain_event_task_updated_placeholder",
            )
            task = board.task(task_id)
        status = _task_status_from_event(data.get("status") or "pending")
        if task:
            task.status = status
            if content:
                task.outputs.append({"kind": "event_update", "content": content[:2000], "created_at": time.time()})
            board.record_change("task_status", {"node_id": task_id, "task_id": task_id, "status": status, "reason": "domain_event"})
        board.ready_nodes()
        save_task_board(board, _run_dir(thread_id, workspace_dir))
        return True

    if event_type == "stage_updated":
        stage_id = str(data.get("stage_id") or "").strip()
        if not stage_id:
            return False
        board = get_or_create_run_state(thread_id, workspace_dir)
        task = _find_task_for_event(board, data, agent=agent)
        if not task:
            return False
        status = _task_status_from_event(data.get("status") or "pending")
        task.status = status
        evidence = _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="stage",
            title=title,
            content=content,
            agent=agent,
            timestamp=event_ts,
            payload={"stage_id": stage_id, "status": data.get("status"), "reason": data.get("reason")},
        )
        _append_task_evidence(task, evidence, output=False)
        board.record_change("task_status", {"node_id": task.id, "task_id": task.id, "status": status, "reason": "stage_event"})
        board.ready_nodes()
        save_task_board(board, _run_dir(thread_id, workspace_dir))
        return True

    evidence = _evidence_from_event(
        event_id=event_id,
        event_type=event_type,
        payload=data,
        title=title,
        content=content,
        agent=agent,
        timestamp=event_ts,
    )
    if not evidence:
        return False

    board = get_or_create_run_state(thread_id, workspace_dir)
    task = _find_task_for_event(board, data, agent=agent, event_type=event_type)
    if not task:
        return False
    _append_task_evidence(
        task,
        evidence,
        output=event_type in {
            "report_ready",
            "done",
            "assistant_message",
            "ephemeral_agent_completed",
            "agent_result_merged",
            "parallel_agent_result",
            "parallel_agents_completed",
        },
    )
    board.record_change(
        "task_evidence_added",
        {
            "node_id": task.id,
            "task_id": task.id,
            "event_id": event_id,
            "event_type": event_type,
            "kind": evidence.get("kind"),
        },
    )
    if event_type == "done":
        _settle_task_board_from_done(board, data)
    save_task_board(board, _run_dir(thread_id, workspace_dir))
    return True

    return False


def rebuild_run_state(thread_id: str, workspace_dir: str, reason: str = "manual") -> RunTaskBoard:
    """Rebuild the mutable task board from the current session execution plan."""
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir) or {}
    board = build_task_board(
        thread_id,
        execution_plan=session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {},
        conversation_id=session.get("conversation_id"),
    )
    board.ready_nodes()
    save_task_board(board, _run_dir(thread_id, workspace_dir))
    store.append_event(
        thread_id,
        "replan_completed",
        title="运行状态地图已重建",
        content=f"Mutable task board rebuilt: {reason}",
        agent="lead",
        payload={"task_count": len(board.nodes), "node_count": len(board.nodes), "reason": reason},
        workspace_dir=workspace_dir,
    )
    return board


def _normalize_task_board(board: RunTaskBoard) -> RunTaskBoard:
    """Apply compatibility cleanup for old persisted task-board artifacts."""
    if board.strategy != "lead_direct_reply" or not board.nodes:
        return board
    board.nodes = []
    board.edges = []
    board.resources = []
    board.gates = []
    board.status = "completed" if board.status in {"created", "running"} else board.status
    board.metadata.update(
        {
            "task_board_suppressed": True,
            "suppressed_reason": "lead_direct_reply",
            "normalized_from_legacy_direct_reply": True,
        }
    )
    board.record_change(
        "task_board_suppressed",
        {"reason": "lead_direct_reply", "normalized_from": "legacy_direct_reply_nodes"},
    )
    return board


def update_task_status(thread_id: str, workspace_dir: str, task_id: str, status: str) -> RunTaskBoard:
    """Apply a task status transition and persist the task board."""
    board = get_or_create_run_state(thread_id, workspace_dir)
    board.apply_node_status(task_id, status)  # type: ignore[arg-type]
    board.ready_nodes()
    save_task_board(board, _run_dir(thread_id, workspace_dir))
    get_event_store().append_event(
        thread_id,
        "task_status_changed",
        title=f"任务状态更新：{task_id}",
        content=f"{task_id} -> {status}",
        agent="lead",
        payload={"node_id": task_id, "task_id": task_id, "status": status, "revision": board.revision},
        workspace_dir=workspace_dir,
    )
    return board


def sync_failures_to_task_board(
    thread_id: str,
    workspace_dir: str,
    failures: list[Any],
    *,
    reason: str = "failure_classifier",
) -> RunTaskBoard:
    """Create/update recovery tasks from classified failure records."""
    board = get_or_create_run_state(thread_id, workspace_dir)
    changed = False
    for failure in failures:
        record = failure.model_dump() if hasattr(failure, "model_dump") else failure
        if not isinstance(record, dict):
            continue
        failure_id = str(record.get("failure_id") or "").strip()
        if not failure_id:
            continue
        task_id = f"task-recovery-{_slug(failure_id)}"
        failed_task = _find_failed_task_for_failure(board, record)
        dependencies = [failed_task.id] if failed_task else []
        action_labels = [
            str(item.get("label") or item.get("action_id") or "")
            for item in record.get("suggested_actions", [])
            if isinstance(item, dict)
        ]
        existing = board.task(task_id)
        recovery_task = RunTask(
            id=task_id,
            type="recovery",
            title=f"恢复：{record.get('title') or record.get('failure_class') or '运行失败'}",
            goal=_recovery_goal(record, action_labels),
            agent_role="lead",
            status=existing.status if existing else "ready",
            dependencies=dependencies,
            can_parallel=False,
            writes_files=False,
            context_policy={
                "mode": "failure",
                "failure_id": failure_id,
                "failure_class": record.get("failure_class"),
                "failed_task_id": failed_task.id if failed_task else "",
                "can_auto_retry": bool(record.get("can_auto_retry")),
            },
            evidence=(existing.evidence if existing else []),
            outputs=(existing.outputs if existing else []),
        )
        evidence = _compact_evidence(
            event_id=failure_id,
            event_type="failure_classified",
            kind="failure",
            title=str(record.get("title") or "运行失败"),
            content=_failure_evidence_summary(record),
            agent="lead",
            timestamp=time.time(),
            payload={
                "failure_id": failure_id,
                "failure_class": record.get("failure_class"),
                "can_auto_retry": record.get("can_auto_retry"),
                "suggested_actions": action_labels[:8],
            },
        )
        _append_task_evidence(recovery_task, evidence, output=True)
        board.add_or_update_task(recovery_task, reason=reason)
        changed = True

    if changed:
        board.ready_nodes()
        save_task_board(board, _run_dir(thread_id, workspace_dir))
        get_event_store().append_event(
            thread_id,
            "recovery_tasks_synced",
            title="恢复任务已同步",
            content=f"已根据 {len(failures)} 条失败记录更新恢复任务。",
            agent="lead",
            payload={"failure_count": len(failures), "revision": board.revision},
            workspace_dir=workspace_dir,
        )
    return board


def build_run_context_pack(
    thread_id: str,
    workspace_dir: str,
    *,
    purpose: str = "lead_global",
    task_id: str | None = None,
    turn_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and persist the run-level ContextPack."""
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir) or {}
    pack = build_context_pack(
        prompt=session.get("prompt", ""),
        team=session.get("team") if isinstance(session.get("team"), list) else [],
        workspace_dir=workspace_dir,
        execution_plan=session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {},
        conversation_id=session.get("conversation_id"),
        thread_id=thread_id,
        turn_context=turn_context,
    )
    data = pack.to_dict()
    data = _stamp_context_pack(data, thread_id, workspace_dir, purpose=purpose, task_id=task_id)
    context_dir = _run_dir(thread_id, workspace_dir) / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(context_dir / "run_context_pack.json", data)
    _write_json_atomic(context_dir / "packs" / f"{data['id']}.json", data)
    store.append_event(
        thread_id,
        "context_pack_built",
        title="上下文包已构建",
        content=f"Selected {len(data.get('selected_files', []))} files.",
        agent="lead",
        payload={
            "selected_file_count": len(data.get("selected_files", [])),
            "used_tokens_estimate": data.get("token_budget", {}).get("used_tokens_estimate", 0),
            "purpose": purpose,
            "task_id": task_id,
            "turn_context": {
                "step": (data.get("turn_context") or {}).get("step")
                if isinstance(data.get("turn_context"), dict) else None,
                "active_task_id": ((data.get("turn_context") or {}).get("active_task") or {}).get("id")
                if isinstance((data.get("turn_context") or {}).get("active_task"), dict) else None,
            },
        },
        workspace_dir=workspace_dir,
    )
    return data


def save_run_context_pack(thread_id: str, workspace_dir: str, data: dict[str, Any]) -> None:
    """Persist an already-built run-level ContextPack."""
    data = _stamp_context_pack(data, thread_id, workspace_dir, purpose=str(data.get("purpose") or "lead_global"))
    context_dir = _run_dir(thread_id, workspace_dir) / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(context_dir / "run_context_pack.json", data)
    _write_json_atomic(context_dir / "packs" / f"{data['id']}.json", data)


def build_task_context_pack(thread_id: str, workspace_dir: str, task_id: str) -> dict[str, Any]:
    """Build and persist a context pack focused on one run-state task."""
    board = get_or_create_run_state(thread_id, workspace_dir)
    task = board.node(task_id)
    if not task:
        raise ValueError(f"Run task not found: {task_id}")
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir) or {}
    node_prompt = " ".join(
        part for part in [
            session.get("prompt", ""),
            task.title,
            task.goal,
            task.agent_role,
        ] if part
    )
    pack = build_context_pack(
        prompt=node_prompt,
        team=session.get("team") if isinstance(session.get("team"), list) else [],
        workspace_dir=workspace_dir,
        execution_plan=session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {},
        conversation_id=session.get("conversation_id"),
        thread_id=thread_id,
    )
    data = pack.to_dict()
    data = _stamp_context_pack(data, thread_id, workspace_dir, purpose="agent_task", task_id=task_id)
    data["task"] = task.model_dump()
    data["graph_node"] = data["task"]
    node_dir = _run_dir(thread_id, workspace_dir) / "context" / "nodes"
    node_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(node_dir / f"{task_id}-context.json", data)
    _write_json_atomic(_run_dir(thread_id, workspace_dir) / "context" / "packs" / f"{data['id']}.json", data)
    store.append_event(
        thread_id,
        "task_context_built",
        title=f"任务上下文已构建：{task.title}",
        content=f"{task_id}: selected {len(data.get('selected_files', []))} files.",
        agent=task.agent_role or "lead",
        payload={"node_id": task_id, "task_id": task_id, "context_file_count": len(data.get("selected_files", []))},
        workspace_dir=workspace_dir,
    )
    return data


def list_context_packs(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """List persisted ContextPack snapshots for one run."""
    context_dir = _run_dir(thread_id, workspace_dir) / "context"
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted((context_dir / "packs").glob("*.json")) if (context_dir / "packs").exists() else []:
        item = _context_pack_index_item(path)
        if item:
            seen.add(str(item.get("id")))
            items.append(item)
    current_path = context_dir / "run_context_pack.json"
    current = _context_pack_index_item(current_path)
    if current and str(current.get("id")) not in seen:
        current["is_current"] = True
        items.insert(0, current)
    else:
        current_id = current.get("id") if isinstance(current, dict) else None
        for item in items:
            if item.get("id") == current_id:
                item["is_current"] = True
    items.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return {
        "thread_id": thread_id,
        "workspace_dir": workspace_dir,
        "total": len(items),
        "context_packs": items,
    }


def get_context_pack_by_id(thread_id: str, workspace_dir: str, pack_id: str) -> dict[str, Any]:
    """Load one persisted ContextPack by id."""
    safe_id = _safe_context_pack_id(pack_id)
    context_dir = _run_dir(thread_id, workspace_dir) / "context"
    candidates = [
        context_dir / "packs" / f"{safe_id}.json",
        context_dir / "run_context_pack.json",
    ]
    for path in candidates:
        data = _read_json(path)
        if data and str(data.get("id") or "") == safe_id:
            return data
    raise ValueError(f"ContextPack not found: {pack_id}")


def preview_context_pack(
    thread_id: str,
    workspace_dir: str,
    *,
    objective: str = "",
) -> dict[str, Any]:
    """Build an unsaved ContextPack preview for a run and optional objective."""
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir) or {}
    prompt = " ".join(
        part
        for part in [session.get("prompt", ""), objective]
        if str(part or "").strip()
    )
    pack = build_context_pack(
        prompt=prompt,
        team=session.get("team") if isinstance(session.get("team"), list) else [],
        workspace_dir=workspace_dir,
        execution_plan=session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {},
        conversation_id=session.get("conversation_id"),
        thread_id=thread_id,
    )
    data = _stamp_context_pack(pack.to_dict(), thread_id, workspace_dir, purpose="preview")
    data["preview"] = True
    data["persisted"] = False
    return data


def get_task_evidence(thread_id: str, workspace_dir: str, task_id: str) -> dict[str, Any]:
    """Return event evidence related to a run-state task."""
    board = get_or_create_run_state(thread_id, workspace_dir)
    task = board.node(task_id)
    if not task:
        raise ValueError(f"Run task not found: {task_id}")
    events = []
    for event in get_event_store().list_events(thread_id, workspace_dir):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("task_id") == task_id or payload.get("node_id") == task_id or event.agent.lower() == task.agent_role.lower():
            events.append(event.model_dump())
    return {
        "thread_id": thread_id,
        "task_id": task_id,
        "node_id": task_id,
        "task": task.model_dump(),
        "node": task.model_dump(),
        "events": events[-50:],
        "total": len(events),
    }


def refresh_summaries(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Generate a deterministic execution summary without mutating run state."""
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir) or {}
    task_view = get_run_tasks_readonly(thread_id, workspace_dir)
    tasks = task_view.get("tasks") if isinstance(task_view.get("tasks"), list) else []
    events = store.list_events(thread_id, workspace_dir)
    status_counts: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.type] = event_counts.get(event.type, 0) + 1
    changed_files = sorted({
        str(event.payload.get("path"))
        for event in events
        if event.type == "file_changed" and isinstance(event.payload, dict) and event.payload.get("path")
    })
    recent_events = [
        {
            "type": event.type,
            "title": event.title,
            "agent": event.agent,
        }
        for event in events[-8:]
    ]
    summary = (
        f"Run strategy={task_view.get('strategy') or session.get('strategy') or 'unknown'}; "
        f"task_source={task_view.get('source')}; tasks={len(tasks)}; "
        f"status_counts={status_counts}; changed_files={changed_files[:12]}; "
        f"recent_events={[item['type'] for item in recent_events]}"
    )
    store.update_session(thread_id, workspace_dir, execution_summary=summary)
    return {
        "thread_id": thread_id,
        "execution_summary": summary,
        "status_counts": status_counts,
        "event_counts": event_counts,
        "changed_files": changed_files,
        "recent_events": recent_events,
        "task_source": task_view.get("source"),
    }


def _stamp_context_pack(
    data: dict[str, Any],
    thread_id: str,
    workspace_dir: str,
    *,
    purpose: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    created_at = float(data.get("created_at") or time.time())
    pack_id = str(data.get("id") or "").strip()
    if not pack_id:
        suffix = uuid.uuid4().hex[:8]
        target = f"-{task_id}" if task_id else ""
        pack_id = _safe_context_pack_id(f"{purpose}{target}-{int(created_at * 1000)}-{suffix}")
    data.update(
        {
            "id": pack_id,
            "thread_id": thread_id,
            "run_id": thread_id,
            "workspace_dir": workspace_dir,
            "purpose": purpose,
            "task_id": task_id,
            "created_at": created_at,
            "schema_version": data.get("schema_version") or 1,
            "persisted": data.get("persisted", True),
        }
    )
    return data


def _context_pack_index_item(path: Path) -> dict[str, Any] | None:
    data = _read_json(path)
    if not data:
        return None
    return {
        "id": data.get("id") or path.stem,
        "purpose": data.get("purpose") or "unknown",
        "task_id": data.get("task_id"),
        "created_at": data.get("created_at") or path.stat().st_mtime,
        "selected_file_count": len(data.get("selected_files", [])) if isinstance(data.get("selected_files"), list) else 0,
        "outline_count": len(data.get("file_outlines", [])) if isinstance(data.get("file_outlines"), list) else 0,
        "used_tokens_estimate": data.get("token_budget", {}).get("used_tokens_estimate", 0)
        if isinstance(data.get("token_budget"), dict) else 0,
        "budget_utilization": data.get("budget_report", {}).get("utilization", 0)
        if isinstance(data.get("budget_report"), dict) else 0,
        "path": str(path),
        "is_current": False,
    }


def _safe_context_pack_id(raw: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(raw).strip())
    text = text.strip("-._")
    return text[:160] or f"context-pack-{uuid.uuid4().hex[:8]}"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _find_task_for_event(
    board: RunTaskBoard,
    payload: dict[str, Any],
    *,
    agent: str = "lead",
    event_type: str = "",
) -> RunTask | None:
    for key in ("task_id", "node_id"):
        task_id = str(payload.get(key) or "").strip()
        if task_id:
            task = board.task(task_id)
            if task:
                return task

    stage_id = str(payload.get("stage_id") or "").strip()
    if stage_id:
        for task in board.nodes:
            if _task_matches_stage(task, stage_id):
                return task

    kind = _kind_for_event(payload, agent=agent, event_type=event_type)
    if event_type in {"report_ready", "done", "assistant_message"}:
        report_tasks = [task for task in board.nodes if task.type == "report"]
        if report_tasks:
            return sorted(report_tasks, key=_task_match_rank)[0]
    role = _agent_role_from_event(
        payload.get("agent_role")
        or payload.get("agent")
        or (payload.get("capability_trace") or {}).get("agent")
        or agent
    )
    candidates = [
        task for task in board.nodes
        if task.agent_role == role or task.type == kind or (kind == "implementation" and task.writes_files)
    ]
    if not candidates and event_type in {"report_ready", "done", "assistant_message"}:
        candidates = [task for task in board.nodes if task.type == "report"]
    if not candidates:
        return None
    return sorted(candidates, key=_task_match_rank)[0]


def _settle_task_board_from_done(board: RunTaskBoard, payload: dict[str, Any]) -> None:
    """Make a terminal run's task board terminal as well."""
    status = str(payload.get("status") or "").lower()
    if status != "completed":
        return

    for task in board.nodes:
        if task.status in {"passed", "failed", "skipped", "cancelled"}:
            continue
        if task.type in {"context_build", "report"}:
            task.status = "passed"
        else:
            task.status = "skipped"
        board.record_change(
            "task_status",
            {
                "node_id": task.id,
                "task_id": task.id,
                "status": task.status,
                "reason": "run_completed",
            },
        )
    board.status = "completed"


def _task_matches_stage(task: RunTask, stage_id: str) -> bool:
    stage = str(stage_id).strip()
    if not stage:
        return False
    slug = _slug(stage)
    return (
        task.id == stage
        or task.id.endswith(f"-{stage}")
        or task.id.endswith(f"-{slug}")
        or task.context_policy.get("stage_id") == stage
    )


def _task_match_rank(task: RunTask) -> tuple[int, int]:
    status_rank = {
        "running": 0,
        "ready": 1,
        "pending": 2,
        "blocked": 3,
        "passed": 4,
        "failed": 5,
        "skipped": 6,
        "cancelled": 7,
    }
    return status_rank.get(task.status, 9), len(task.evidence)


def _kind_for_event(payload: dict[str, Any], *, agent: str, event_type: str) -> str:
    if event_type.startswith("ephemeral_agent_") or event_type.startswith("parallel_agent") or event_type.startswith("agent_run") or event_type == "agent_result_merged":
        return _task_type_from_agent_role(payload.get("role") or agent)
    if event_type == "test_finished":
        return "test"
    if event_type in {"report_ready", "done", "assistant_message"}:
        return "report"
    if event_type in {"file_changed", "diff_updated"}:
        return "implementation"
    tool = str(payload.get("tool") or "").lower()
    if tool in {"write_file", "edit_file"}:
        return "implementation"
    if tool in {"bash", "run_command"}:
        command = str((payload.get("input") or {}).get("command") if isinstance(payload.get("input"), dict) else "").lower()
        if any(marker in command for marker in ("test", "pytest", "vitest", "jest", "lint")):
            return "test"
        return "implementation"
    if tool in {"task_create", "project_context", "search_codebase", "list_directory"}:
        return "plan"
    role = _agent_role_from_event(agent)
    if role == "coder":
        return "implementation"
    if role == "tester":
        return "test"
    if role == "reviewer":
        return "review"
    if role == "planner":
        return "plan"
    return "analysis"


def _evidence_from_event(
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    title: str,
    content: str,
    agent: str,
    timestamp: float,
) -> dict[str, Any] | None:
    if event_type not in EVIDENCE_EVENT_TYPES:
        return None
    if (
        event_type.startswith("ephemeral_agent_")
        or event_type in {"agent_run_started", "agent_result_merged", "agent_run_failed"}
        or event_type.startswith("parallel_agent")
        or event_type == "parallel_agents_completed"
    ):
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        agent_data = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
        if not result and isinstance(agent_data.get("result"), dict):
            result = agent_data.get("result") or {}
        evidence_items = result.get("evidence") if isinstance(result.get("evidence"), list) else []
        risks = result.get("risks") if isinstance(result.get("risks"), list) else []
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        summary = str(result.get("summary") or content or "")
        return _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="agent_result" if "result" in event_type or event_type == "ephemeral_agent_completed" else "agent_activity",
            title=title,
            content=summary,
            agent=agent,
            timestamp=timestamp,
            payload={
                "agent_id": payload.get("agent_id") or agent_data.get("agent_id"),
                "agent_name": payload.get("name") or agent_data.get("name") or agent,
                "agent_role": payload.get("role") or agent_data.get("role"),
                "status": payload.get("status") or agent_data.get("status"),
                "terminal_status": payload.get("terminal_status") or agent_data.get("terminal_status"),
                "mode": payload.get("mode"),
                "duration_ms": payload.get("duration_ms"),
                "evidence_count": len(evidence_items),
                "risk_count": len(risks),
                "artifact_count": len(artifacts),
                "artifacts": artifacts[:8],
            },
        )
    if event_type == "tool_call_finished":
        trace = payload.get("capability_trace") if isinstance(payload.get("capability_trace"), dict) else {}
        output = str(payload.get("output") or content or "")
        tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        changed_files = payload.get("changed_files") if isinstance(payload.get("changed_files"), list) else []
        target_path = _tool_target_path(payload, tool_input)
        command = str(tool_input.get("command") or payload.get("command") or "")
        return _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="tool_call",
            title=title,
            content=output,
            agent=agent,
            timestamp=timestamp,
            payload={
                "tool": payload.get("tool"),
                "ok": not is_tool_error_output(output),
                "stage_id": payload.get("stage_id"),
                "task_id": payload.get("task_id"),
                "path": target_path,
                "command": command[:240],
                "duration_ms": payload.get("duration_ms") or payload.get("elapsed_ms"),
                "changed_files": [str(path) for path in changed_files[:12]],
                "stdout_tail": str(payload.get("stdout") or "")[-500:],
                "stderr_tail": str(payload.get("stderr") or "")[-500:],
                "capability_id": trace.get("capability_id"),
                "capability_name": trace.get("capability_name"),
            },
        )
    if event_type == "file_changed":
        return _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="file_change",
            title=title,
            content=content,
            agent=agent,
            timestamp=timestamp,
            payload={
                "path": payload.get("path"),
                "change_type": payload.get("change_type"),
                "tool": payload.get("tool"),
            },
        )
    if event_type == "diff_updated":
        changed_files = payload.get("changed_files") if isinstance(payload.get("changed_files"), list) else []
        return _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="diff",
            title=title,
            content=content,
            agent=agent,
            timestamp=timestamp,
            payload={"changed_files_count": len(changed_files), "source": payload.get("source")},
        )
    if event_type == "test_finished":
        checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
        return _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="test",
            title=title,
            content=content,
            agent=agent,
            timestamp=timestamp,
            payload={"status": payload.get("status"), "checks_count": len(checks)},
        )
    if event_type in {"quality_gate", "delivery_scored"}:
        return _compact_evidence(
            event_id=event_id,
            event_type=event_type,
            kind="quality",
            title=title,
            content=content,
            agent=agent,
            timestamp=timestamp,
            payload={"status": payload.get("status"), "score": payload.get("score")},
        )
    return _compact_evidence(
        event_id=event_id,
        event_type=event_type,
        kind="report",
        title=title,
        content=content,
        agent=agent,
        timestamp=timestamp,
        payload={"status": payload.get("status")},
    )


def _compact_evidence(
    *,
    event_id: str,
    event_type: str,
    kind: str,
    title: str,
    content: str,
    agent: str,
    timestamp: float,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "event_id": event_id,
        "event_type": event_type,
        "kind": kind,
        "title": str(title or event_type)[:200],
        "agent": _agent_role_from_event(agent),
        "timestamp": timestamp,
        "summary": str(content or "")[:500],
    }
    for key, value in payload.items():
        if value not in (None, "", []):
            result[key] = value
    return result


def _tool_target_path(payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    for source in (payload, tool_input):
        for key in ("path", "file", "target", "target_path", "rel_path", "relative_path"):
            value = source.get(key)
            if value:
                return str(value)
    changed_files = payload.get("changed_files")
    if isinstance(changed_files, list) and changed_files:
        return str(changed_files[0])
    return ""


def _append_task_evidence(task: RunTask, evidence: dict[str, Any], *, output: bool) -> None:
    event_id = evidence.get("event_id")
    if event_id and any(item.get("event_id") == event_id for item in task.evidence):
        return
    task.evidence.append(evidence)
    task.evidence = task.evidence[-30:]
    if output:
        task.outputs.append(evidence)
        task.outputs = task.outputs[-20:]


def _task_status_from_event(status: Any) -> str:
    return TASK_EVENT_STATUS_MAP.get(str(status or "").strip().lower(), "pending")


def _agent_role_from_event(agent: Any) -> str:
    role = str(agent or "lead").strip().lower()
    if role in {"planner", "coder", "tester", "reviewer", "security", "lead"}:
        return role
    if "test" in role:
        return "tester"
    if "review" in role:
        return "reviewer"
    if "plan" in role:
        return "planner"
    if any(marker in role for marker in ("code", "backend", "frontend", "worker", "docs", "design")):
        return "coder"
    return "lead"


def _task_type_from_agent_role(agent: Any) -> str:
    role = str(agent or "").lower()
    if "test" in role or "qa" in role:
        return "test"
    if "review" in role or "security" in role:
        return "review"
    if "plan" in role:
        return "plan"
    if any(marker in role for marker in ("code", "backend", "frontend", "worker", "docs", "design", "action")):
        return "implementation"
    return "analysis"


def _task_type_from_event(task_data: dict[str, Any], agent: str) -> str:
    role = _agent_role_from_event(task_data.get("owner") or task_data.get("agent_role") or agent)
    text = f"{task_data.get('title', '')} {task_data.get('description', '')}".lower()
    if role == "coder" or "实现" in text or "代码" in text or "edit" in text:
        return "implementation"
    if role == "tester" or "测试" in text or "verify" in text:
        return "test"
    if role == "reviewer" or "审查" in text or "review" in text:
        return "review"
    if role == "planner" or "计划" in text or "规划" in text:
        return "plan"
    return "analysis"


def _slug(value: str) -> str:
    chars = []
    for char in str(value).lower().replace("_", "-").replace(" ", "-"):
        if char.isalnum() or char == "-":
            chars.append(char)
    return ("".join(chars).strip("-") or "node")[:40]


def _find_failed_task_for_failure(board: RunTaskBoard, record: dict[str, Any]) -> RunTask | None:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    for key in ("task_id", "node_id"):
        value = str(evidence.get(key) or "").strip()
        if value and board.task(value):
            return board.task(value)
    stage_id = str(evidence.get("stage_id") or "").strip()
    if stage_id:
        for task in board.nodes:
            if _task_matches_stage(task, stage_id):
                return task
    failed = [task for task in board.nodes if task.status == "failed"]
    if failed:
        return failed[0]
    failure_class = str(record.get("failure_class") or "")
    if failure_class == "test_failure":
        return next((task for task in board.nodes if task.type == "test"), None)
    if failure_class in {"patch_error", "workspace_error"}:
        return next((task for task in board.nodes if task.type == "implementation"), None)
    return None


def _recovery_goal(record: dict[str, Any], action_labels: list[str]) -> str:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    detail = str(evidence.get("event_content") or evidence.get("output") or evidence.get("error_detail") or "")[:500]
    actions = "；".join(label for label in action_labels if label)
    parts = [
        f"失败类型：{record.get('failure_class') or 'unknown'}。",
        f"证据：{detail}" if detail else "",
        f"建议动作：{actions}" if actions else "",
    ]
    return " ".join(part for part in parts if part)


def _failure_evidence_summary(record: dict[str, Any]) -> str:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return str(
        evidence.get("event_content")
        or evidence.get("output")
        or evidence.get("error_detail")
        or record.get("title")
        or ""
    )[:500]


update_node_status = update_task_status
get_or_create_run_graph = get_or_create_run_state
rebuild_run_graph = rebuild_run_state
build_node_context_pack = build_task_context_pack
get_node_evidence = get_task_evidence
