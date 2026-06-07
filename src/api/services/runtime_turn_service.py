"""Execute one observable turn of the Lead runtime loop.

The service keeps the controller authoritative for a single decision while
allowing the existing agent engine to remain an execution adapter during the
incremental runtime migration.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import fields
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from src.agent.context_pack import ContextPack
from src.agent.decision_protocol import LeadAction
from src.api.services.agent_loop_controller_service import (
    get_loop_observation,
    propose_next_loop_action,
    run_loop_controller_step,
)
from src.api.services.event_store import get_event_store
from src.api.services.run_state_service import build_run_context_pack


RuntimeTurnExecutor = Callable[
    [LeadAction, dict[str, Any]],
    dict[str, Any] | str | Awaitable[dict[str, Any] | str],
]


class RuntimeTurnResult(BaseModel):
    """Durable result of one observation-to-execution runtime turn."""

    turn_id: str
    step: int
    observation: dict[str, Any] = Field(default_factory=dict)
    context_pack_id: str | None = None
    proposed_action: LeadAction
    selected_action: LeadAction
    repaired: bool = False
    execution_result: dict[str, Any] = Field(default_factory=dict)
    finish_readiness: dict[str, Any] = Field(default_factory=dict)
    terminal_status: str | None = None


async def run_runtime_turn(
    thread_id: str,
    workspace_dir: str,
    *,
    action: dict[str, Any] | LeadAction | None = None,
    executor: RuntimeTurnExecutor | None = None,
    execute_tools: bool = False,
    rebuild_context: bool = True,
) -> RuntimeTurnResult:
    """Run one controller-owned turn and persist its evidence as events."""
    store = get_event_store()
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    observation = get_loop_observation(thread_id, workspace_dir)
    loop = observation.get("loop") if isinstance(observation.get("loop"), dict) else {}
    step = int(loop.get("current_step") or 0) + 1

    _append_turn_event(
        store,
        thread_id,
        workspace_dir,
        "loop_turn_started",
        "Agent Loop 轮次开始",
        turn_id=turn_id,
        step=step,
        payload={"event_count": observation.get("event_count", 0)},
    )

    context_pack: dict[str, Any] = {}
    context_pack_id: str | None = None
    turn_context = _turn_context_from_observation(observation, turn_id=turn_id, step=step)
    if rebuild_context:
        try:
            context_pack = build_run_context_pack(
                thread_id,
                workspace_dir,
                purpose="lead_turn",
                task_id=turn_context.get("active_task", {}).get("id")
                if isinstance(turn_context.get("active_task"), dict) else None,
                turn_context=turn_context,
            )
            context_pack_id = str(context_pack.get("id") or "") or None
            _append_turn_event(
                store,
                thread_id,
                workspace_dir,
                "loop_context_built",
                "Agent Loop 上下文已构建",
                turn_id=turn_id,
                step=step,
                context_pack_id=context_pack_id,
                payload={
                    "selected_file_count": len(context_pack.get("selected_files", [])),
                    "relevant_files": context_pack.get("relevant_files", []),
                    "turn_context": {
                        "active_task_id": turn_context.get("active_task", {}).get("id")
                        if isinstance(turn_context.get("active_task"), dict) else None,
                        "recent_tool_result_count": len(turn_context.get("recent_tool_results", []))
                        if isinstance(turn_context.get("recent_tool_results"), list) else 0,
                    },
                },
            )
        except Exception as exc:
            _append_turn_event(
                store,
                thread_id,
                workspace_dir,
                "loop_context_failed",
                "Agent Loop 上下文构建失败",
                turn_id=turn_id,
                step=step,
                payload={"error": str(exc)},
            )

    proposed = action.model_dump() if isinstance(action, LeadAction) else action
    if not isinstance(proposed, dict) or not proposed:
        proposed = propose_next_loop_action(observation)
    _append_turn_event(
        store,
        thread_id,
        workspace_dir,
        "loop_action_proposed",
        "Lead 已提出本轮动作",
        turn_id=turn_id,
        step=step,
        context_pack_id=context_pack_id,
        action_type=str(proposed.get("type") or ""),
        task_id=proposed.get("task_id"),
        payload={"action": proposed},
    )

    controller_result = run_loop_controller_step(
        thread_id,
        workspace_dir,
        action=proposed,
        commit=True,
        auto_repair=True,
        execute_tools=execute_tools,
        context_pack_id=context_pack_id,
    )
    proposed_action = LeadAction.model_validate(controller_result["candidate_action"])
    selected_action = LeadAction.model_validate(controller_result["selected_action"])
    repaired = bool(controller_result.get("repaired"))
    if repaired:
        _append_turn_event(
            store,
            thread_id,
            workspace_dir,
            "loop_action_repaired",
            "Lead 动作已自动修复",
            turn_id=turn_id,
            step=step,
            context_pack_id=context_pack_id,
            action_type=selected_action.type,
            task_id=selected_action.task_id,
            payload={
                "proposed_action": proposed_action.model_dump(),
                "selected_action": selected_action.model_dump(),
                "check": controller_result.get("check", {}),
            },
        )

    execution_result = _normalize_execution_result(controller_result.get("tool_execution"))
    if controller_result.get("committed") and executor is not None:
        raw_result = executor(selected_action, context_pack)
        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        execution_result = _normalize_execution_result(raw_result)
    elif not controller_result.get("committed"):
        execution_result = {
            "executed": False,
            "result": "rejected",
            "reason": str(controller_result.get("check", {}).get("reason") or "Action was not committed."),
        }

    _append_turn_event(
        store,
        thread_id,
        workspace_dir,
        "loop_action_executed",
        "Lead 动作已执行",
        turn_id=turn_id,
        step=step,
        context_pack_id=context_pack_id,
        action_type=selected_action.type,
        task_id=selected_action.task_id,
        payload={"result": execution_result},
    )

    refreshed = get_loop_observation(thread_id, workspace_dir)
    refreshed_loop = refreshed.get("loop") if isinstance(refreshed.get("loop"), dict) else {}
    result = RuntimeTurnResult(
        turn_id=turn_id,
        step=step,
        observation=observation,
        context_pack_id=context_pack_id,
        proposed_action=proposed_action,
        selected_action=selected_action,
        repaired=repaired,
        execution_result=execution_result,
        finish_readiness=refreshed.get("finish_readiness", {}),
        terminal_status=refreshed_loop.get("terminal_status"),
    )
    _append_turn_event(
        store,
        thread_id,
        workspace_dir,
        "loop_turn_finished",
        "Agent Loop 轮次完成",
        turn_id=turn_id,
        step=step,
        context_pack_id=context_pack_id,
        action_type=selected_action.type,
        task_id=selected_action.task_id,
        payload={"turn_result": result.model_dump(mode="json")},
    )
    return result


def context_pack_to_text(data: dict[str, Any] | None) -> str:
    """Render a persisted ContextPack dictionary for the model prompt."""
    source = data if isinstance(data, dict) else {}
    allowed = {field.name for field in fields(ContextPack)}
    return ContextPack(**{key: value for key, value in source.items() if key in allowed}).to_text()


def _normalize_execution_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {"executed": False, "result": "not_requested"}
    return {"executed": True, "result": "success", "output": str(value)}


def _turn_context_from_observation(
    observation: dict[str, Any],
    *,
    turn_id: str,
    step: int,
) -> dict[str, Any]:
    """Extract compact per-turn signals for ContextPack construction."""
    task_board = observation.get("task_board") if isinstance(observation.get("task_board"), dict) else {}
    tasks = task_board.get("tasks") if isinstance(task_board.get("tasks"), list) else []
    active_task = _first_active_task(tasks)
    readiness = observation.get("finish_readiness") if isinstance(observation.get("finish_readiness"), dict) else {}
    counts = readiness.get("counts") if isinstance(readiness.get("counts"), dict) else _status_counts(tasks)
    recent_events = observation.get("recent_events") if isinstance(observation.get("recent_events"), list) else []
    loop = observation.get("loop") if isinstance(observation.get("loop"), dict) else {}
    return {
        "turn_id": turn_id,
        "step": step,
        "active_task": active_task or {},
        "failed_tasks": _tasks_with_status(tasks, {"failed", "blocked", "cancelled"}, limit=6),
        "task_status_counts": counts,
        "recent_event_types": [
            str(event.get("type") or "")
            for event in recent_events
            if isinstance(event, dict) and event.get("type")
        ][-12:],
        "recent_tool_results": _recent_tool_results(recent_events),
        "changed_files": _changed_files_from_observation(active_task, recent_events),
        "loop": {
            "current_step": loop.get("current_step"),
            "active_agent": loop.get("active_agent"),
            "terminal_status": loop.get("terminal_status"),
            "context_pack_id": loop.get("context_pack_id"),
        },
    }


def _first_active_task(tasks: list[Any]) -> dict[str, Any] | None:
    for status in ("running", "ready", "pending", "blocked"):
        for task in tasks:
            if not isinstance(task, dict) or str(task.get("status") or "") != status:
                continue
            return {
                "id": task.get("id"),
                "title": task.get("title"),
                "goal": task.get("goal"),
                "status": task.get("status"),
                "type": task.get("type") or task.get("kind"),
                "agent_role": task.get("agent_role") or task.get("agent"),
                "acceptance": _compact_task_items(task.get("acceptance"), limit=6),
                "recent_evidence": _compact_task_items(task.get("evidence_preview"), limit=6),
                "recent_outputs": _compact_task_items(task.get("output_preview"), limit=4),
            }
    return None


def _tasks_with_status(
    tasks: list[Any],
    statuses: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or str(task.get("status") or "") not in statuses:
            continue
        result.append({
            "id": task.get("id"),
            "title": task.get("title"),
            "goal": task.get("goal"),
            "status": task.get("status"),
            "type": task.get("type") or task.get("kind"),
            "agent_role": task.get("agent_role") or task.get("agent"),
            "recent_evidence": _compact_task_items(task.get("evidence_preview"), limit=4),
            "recent_outputs": _compact_task_items(task.get("output_preview"), limit=3),
        })
        if len(result) >= limit:
            break
    return result


def _status_counts(tasks: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _recent_tool_results(events: list[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    interesting_types = {
        "tool_call_finished",
        "tool_call_failed",
        "file_changed",
        "diff_updated",
        "test_finished",
        "quality_gate",
        "action_executed",
        "loop_action_executed",
        "agent_result_merged",
        "parallel_agent_result",
    }
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type not in interesting_types and "tool" not in event_type and "file" not in event_type:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        tool_call = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else {}
        target = (
            payload.get("target")
            or payload.get("path")
            or result.get("target")
            or result.get("path")
            or tool_call.get("target")
        )
        changed_files = payload.get("changed_files") or result.get("changed_files")
        item = {
            "type": event_type,
            "title": event.get("title"),
            "agent": event.get("agent"),
            "task_id": payload.get("task_id") or payload.get("node_id"),
            "tool": payload.get("tool") or payload.get("kind") or result.get("kind") or tool_call.get("tool"),
            "target": target,
            "status": payload.get("status") or result.get("status") or result.get("result"),
            "summary": payload.get("summary") or payload.get("content") or result.get("summary") or event.get("title"),
            "changed_files": changed_files if isinstance(changed_files, list) else [],
        }
        results.append({key: value for key, value in item.items() if value not in (None, "", [])})
    return results[-8:]


def _compact_task_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[-limit:]:
        if not isinstance(item, dict):
            continue
        compact = {
            "id": str(item.get("id") or item.get("event_id") or "")[:120],
            "kind": str(item.get("kind") or item.get("type") or "")[:100],
            "status": str(item.get("status") or "")[:100],
            "title": str(item.get("title") or "")[:240],
            "description": str(item.get("description") or "")[:500],
            "content": str(item.get("content") or item.get("summary") or "")[:500],
            "path": str(item.get("path") or "")[:240],
            "tool": str(item.get("tool") or "")[:100],
        }
        changed_files = item.get("changed_files")
        if isinstance(changed_files, list):
            compact["changed_files"] = [str(path)[:240] for path in changed_files[:8]]
        compact = {key: value for key, value in compact.items() if value not in ("", [])}
        if compact:
            result.append(compact)
    return result


def _changed_files_from_observation(
    active_task: dict[str, Any] | None,
    events: list[Any],
) -> list[str]:
    paths: list[str] = []
    if isinstance(active_task, dict):
        for field in ("recent_evidence", "recent_outputs"):
            items = active_task.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("path"):
                    paths.append(str(item["path"]))
                changed_files = item.get("changed_files")
                if isinstance(changed_files, list):
                    paths.extend(str(path) for path in changed_files if path)
    for item in _recent_tool_results(events):
        if item.get("target") and item.get("type") in {"file_changed", "diff_updated"}:
            paths.append(str(item["target"]))
        changed_files = item.get("changed_files")
        if isinstance(changed_files, list):
            paths.extend(str(path) for path in changed_files if path)
    return list(dict.fromkeys(path for path in paths if path))[:20]


def _append_turn_event(
    store: Any,
    thread_id: str,
    workspace_dir: str,
    event_type: str,
    title: str,
    *,
    turn_id: str,
    step: int,
    context_pack_id: str | None = None,
    action_type: str = "",
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    event_payload = {
        "turn_id": turn_id,
        "step": step,
        "context_pack_id": context_pack_id,
        "action_type": action_type,
        "task_id": task_id,
        **(payload or {}),
    }
    store.append_event(
        thread_id,
        event_type,
        title=title,
        content=str(event_payload.get("result") or ""),
        agent="lead",
        payload=event_payload,
        workspace_dir=workspace_dir,
    )
