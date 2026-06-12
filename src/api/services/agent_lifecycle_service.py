"""Run-scoped Agent lifecycle projection.

The ephemeral-agent service owns mutations.  This module owns a read-only
projection that frontend, reports, and tests can consume consistently.
"""

from __future__ import annotations

import json
from typing import Any

from src.api.services.event_store import get_event_store

ACTIVE_LIFECYCLE_STATUSES = {"suggested", "active", "working", "waiting_input", "running"}
ARCHIVED_LIFECYCLE_STATUSES = {"completed", "archived", "failed", "expired", "cancelled"}


def build_agent_lifecycle_projection(
    thread_id: str,
    workspace_dir: str,
    *,
    agents: list[dict[str, Any]] | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return a stable lifecycle view for all temporary Agents in one run."""
    base_agents = [dict(agent) for agent in (agents or []) if isinstance(agent, dict)]
    event_refs = _agent_event_refs(thread_id, workspace_dir)
    step_refs = _spawn_tool_loop_step_refs(thread_id, workspace_dir)

    projected: list[dict[str, Any]] = []
    for agent in base_agents:
        agent_id = str(agent.get("agent_id") or "")
        refs = event_refs.get(agent_id, [])
        projection = _project_agent(agent, refs, step_refs.get(agent_id, {}))
        if include_archived or projection["lifecycle_status"] not in ARCHIVED_LIFECYCLE_STATUSES:
            projected.append(projection)

    known_ids = {str(agent.get("agent_id") or "") for agent in projected}
    for agent_id, refs in event_refs.items():
        if not agent_id or agent_id in known_ids:
            continue
        projection = _project_agent(_agent_from_events(thread_id, agent_id, refs), refs, step_refs.get(agent_id, {}))
        if include_archived or projection["lifecycle_status"] not in ARCHIVED_LIFECYCLE_STATUSES:
            projected.append(projection)

    projected.sort(key=lambda item: (float(item.get("created_at") or 0), str(item.get("agent_id") or "")))
    active = [agent for agent in projected if agent["lifecycle_status"] in ACTIVE_LIFECYCLE_STATUSES]
    archived = [agent for agent in projected if agent["lifecycle_status"] in ARCHIVED_LIFECYCLE_STATUSES]
    return {
        "thread_id": thread_id,
        "agents": projected,
        "active": active,
        "archived": archived,
        "timeline": _timeline(event_refs),
        "counts": {
            "total": len(projected),
            "active": len(active),
            "archived": len(archived),
            "working": sum(1 for agent in projected if agent["lifecycle_status"] in {"working", "running"}),
            "completed": sum(1 for agent in projected if agent["lifecycle_status"] == "completed"),
            "failed": sum(1 for agent in projected if agent["lifecycle_status"] == "failed"),
        },
    }


def _project_agent(
    agent: dict[str, Any],
    refs: list[dict[str, Any]],
    step_ref: dict[str, Any],
) -> dict[str, Any]:
    status = str(agent.get("status") or "")
    terminal = str(agent.get("terminal_status") or "")
    last_ref = refs[-1] if refs else {}
    lifecycle_status = _lifecycle_status(agent, last_ref)
    loop_step_id = str(
        agent.get("loop_step_id")
        or last_ref.get("loop_step_id")
        or step_ref.get("loop_step_id")
        or ""
    )
    projected = {
        **agent,
        "lifecycle_status": lifecycle_status,
        "display_status": _display_status(lifecycle_status),
        "is_active": lifecycle_status in ACTIVE_LIFECYCLE_STATUSES,
        "is_archived": lifecycle_status in ARCHIVED_LIFECYCLE_STATUSES,
        "status": status or lifecycle_status,
        "terminal_status": terminal,
        "last_event_type": str(last_ref.get("event_type") or ""),
        "last_event_id": str(last_ref.get("event_id") or ""),
        "last_event_at": float(last_ref.get("timestamp") or agent.get("last_active_at") or agent.get("created_at") or 0),
        "loop_step_id": loop_step_id,
        "loop_action_type": str(last_ref.get("loop_action_type") or step_ref.get("loop_action_type") or ""),
        "event_count": len(refs),
    }
    return projected


def _lifecycle_status(agent: dict[str, Any], last_ref: dict[str, Any]) -> str:
    event_type = str(last_ref.get("event_type") or "")
    terminal = str(agent.get("terminal_status") or "")
    status = str(agent.get("status") or "")
    if event_type in {"parallel_agent_failed", "agent_run_failed"}:
        return "failed"
    if terminal == "expired" or status == "expired" or event_type == "ephemeral_agent_expired":
        return "expired"
    if terminal == "completed" or event_type in {"ephemeral_agent_completed", "parallel_agent_result", "agent_result_merged"}:
        return "completed"
    if terminal in {"cancelled", "canceled"}:
        return "cancelled"
    if status == "archived" or event_type == "ephemeral_agent_archived":
        return "archived"
    if status in {"working", "running"} or event_type in {"ephemeral_agent_updated", "parallel_agent_progress", "agent_run_started"}:
        return "working"
    if status in {"suggested", "active", "waiting_input"}:
        return status
    return status or "active"


def _display_status(status: str) -> str:
    return {
        "suggested": "建议中",
        "active": "已创建",
        "working": "工作中",
        "running": "运行中",
        "waiting_input": "等待输入",
        "completed": "已完成",
        "archived": "已归档",
        "failed": "失败",
        "expired": "已过期",
        "cancelled": "已取消",
    }.get(status, status or "未知")


def _agent_event_refs(thread_id: str, workspace_dir: str) -> dict[str, list[dict[str, Any]]]:
    refs: dict[str, list[dict[str, Any]]] = {}
    for event in get_event_store().list_events(thread_id, workspace_dir):
        event_type = str(event.type or "")
        if not _is_agent_lifecycle_event(event_type):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        agent_id = _agent_id_from_payload(payload)
        if not agent_id:
            continue
        ref = {
            "event_id": event.id,
            "event_type": event_type,
            "timestamp": event.timestamp,
            "agent": event.agent,
            "name": payload.get("name") or _nested(payload, "agent", "name"),
            "role": payload.get("role") or _nested(payload, "agent", "role"),
            "status": payload.get("status") or _nested(payload, "agent", "status"),
            "terminal_status": payload.get("terminal_status") or _nested(payload, "agent", "terminal_status"),
            "loop_step_id": payload.get("loop_step_id") or _nested(payload, "agent", "loop_step_id"),
            "loop_action_type": payload.get("loop_action_type") or _nested(payload, "agent", "loop_action_type"),
            "content": event.content,
        }
        refs.setdefault(agent_id, []).append(ref)
    for items in refs.values():
        items.sort(key=lambda item: float(item.get("timestamp") or 0))
    return refs


def _spawn_tool_loop_step_refs(thread_id: str, workspace_dir: str) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for event in get_event_store().list_events(thread_id, workspace_dir):
        if event.type != "tool_call_finished":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("tool") or "") != "spawn_agent":
            continue
        agent_id = _agent_id_from_tool_output(payload)
        if not agent_id:
            continue
        refs[agent_id] = {
            "loop_step_id": str(payload.get("loop_step_id") or ""),
            "loop_action_type": str(payload.get("loop_action_type") or ""),
            "event_id": event.id,
        }
    return refs


def _agent_id_from_tool_output(payload: dict[str, Any]) -> str:
    output = str(payload.get("output") or "")
    try:
        data = json.loads(output) if output else {}
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict):
        return str(data.get("agent_id") or data.get("id") or "")
    return ""


def _agent_from_events(thread_id: str, agent_id: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    first = refs[0] if refs else {}
    last = refs[-1] if refs else {}
    return {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "name": str(last.get("name") or first.get("name") or agent_id),
        "role": str(last.get("role") or first.get("role") or "worker"),
        "status": str(last.get("status") or ""),
        "terminal_status": str(last.get("terminal_status") or ""),
        "created_at": float(first.get("timestamp") or 0),
        "last_active_at": float(last.get("timestamp") or 0),
        "result": {},
    }


def _timeline(refs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for agent_id, events in refs.items():
        for event in events:
            items.append({"agent_id": agent_id, **event})
    items.sort(key=lambda item: float(item.get("timestamp") or 0))
    return items


def _is_agent_lifecycle_event(event_type: str) -> bool:
    return (
        event_type.startswith("ephemeral_agent_")
        or event_type.startswith("parallel_agent")
        or event_type.startswith("agent_run")
        or event_type
        in {
            "agent_result_merged",
            "agent_context_pack_built",
            "agent_context_pack_failed",
            "agent_evidence_pack_built",
            "agent_result_merge_recorded",
            "agent_result_merge_record_failed",
        }
    )


def _agent_id_from_payload(payload: dict[str, Any]) -> str:
    return str(
        payload.get("agent_id")
        or _nested(payload, "agent", "agent_id")
        or _nested(payload, "result", "agent_id")
        or ""
    )


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value
