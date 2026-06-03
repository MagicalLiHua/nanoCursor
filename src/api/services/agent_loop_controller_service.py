"""Runtime step controller for the Lead Agent loop.

The controller is intentionally small: it does not execute tools or become a
workflow graph. It observes the current run, proposes or checks one Lead action,
optionally commits that action to the loop ledger, and returns the refreshed
state for the caller to decide the next turn.
"""

from __future__ import annotations

from typing import Any

from src.agent.decision_protocol import LeadAction
from src.api.services.agent_loop_state_service import (
    AgentLoopState,
    append_loop_step,
    check_loop_action,
    get_agent_loop_state,
)
from src.api.services.event_store import get_event_store
from src.api.services.run_state_service import get_run_tasks_readonly


def get_loop_observation(thread_id: str, workspace_dir: str, *, event_limit: int = 12) -> dict[str, Any]:
    """Return the read-only state the controller uses before proposing an action."""
    loop = get_agent_loop_state(thread_id, workspace_dir)
    task_board = get_run_tasks_readonly(thread_id, workspace_dir)
    events = get_event_store().list_events(thread_id, workspace_dir)
    recent_events = [
        {
            "id": event.id,
            "type": event.type,
            "title": event.title,
            "agent": event.agent,
            "timestamp": event.timestamp,
            "payload": event.payload,
        }
        for event in events[-max(event_limit, 0):]
    ]
    return {
        "thread_id": thread_id,
        "workspace_dir": workspace_dir,
        "loop": loop,
        "task_board": task_board,
        "finish_readiness": loop.get("finish_readiness", {}),
        "next_actions": loop.get("next_actions", []),
        "recent_events": recent_events,
        "event_count": len(events),
    }


def run_loop_controller_step(
    thread_id: str,
    workspace_dir: str,
    *,
    action: dict[str, Any] | None = None,
    commit: bool = False,
    auto_repair: bool = True,
    execute_tools: bool = False,
) -> dict[str, Any]:
    """Run one observe-check-repair-commit controller step."""
    observation = get_loop_observation(thread_id, workspace_dir)
    candidate = action if isinstance(action, dict) and action else propose_next_loop_action(observation)
    initial_check = check_loop_action(thread_id, workspace_dir, candidate)

    selected_action = candidate
    selected_check = initial_check
    repaired = False
    if auto_repair and not initial_check.get("allowed") and isinstance(initial_check.get("repaired_action"), dict):
        repaired_action = initial_check["repaired_action"]
        repair_check = check_loop_action(thread_id, workspace_dir, repaired_action)
        if repair_check.get("allowed"):
            selected_action = repaired_action
            selected_check = repair_check
            repaired = True

    committed = False
    step: dict[str, Any] | None = None
    tool_execution: dict[str, Any] | None = None
    if commit and selected_check.get("allowed"):
        state = append_loop_step(
            thread_id,
            workspace_dir,
            action=selected_action,
            phase=_phase_for_action(str(selected_action.get("type") or "")),
            status="waiting" if selected_action.get("type") == "request_approval" else "completed",
            summary=_summary_for_action(selected_action, repaired=repaired),
        )
        committed = True
        step = state.steps[-1].model_dump() if state.steps else None
        if execute_tools and selected_action.get("type") == "call_tool":
            tool_execution = _execute_loop_tool_action(thread_id, workspace_dir, selected_action)
        get_event_store().append_event(
            thread_id,
            "agent_loop_controller_step",
            title="Agent Loop 控制步骤已提交",
            content=step.get("summary", "") if isinstance(step, dict) else "",
            agent="lead",
            payload={
                "committed": True,
                "repaired": repaired,
                "selected_action": selected_action,
                "check": selected_check,
                "tool_execution": tool_execution,
            },
            workspace_dir=workspace_dir,
        )

    refreshed_observation = get_loop_observation(thread_id, workspace_dir)
    return {
        "thread_id": thread_id,
        "committed": committed,
        "repaired": repaired,
        "candidate_action": candidate,
        "selected_action": selected_action,
        "initial_check": initial_check,
        "check": selected_check,
        "step": step,
        "tool_execution": tool_execution,
        "loop": refreshed_observation["loop"],
        "task_board": refreshed_observation["task_board"],
        "observation": refreshed_observation,
    }


def propose_next_loop_action(observation: dict[str, Any]) -> dict[str, Any]:
    """Propose a conservative next action from the current observation."""
    loop = observation.get("loop") if isinstance(observation.get("loop"), dict) else {}
    state = AgentLoopState.model_validate(loop)
    readiness = observation.get("finish_readiness") if isinstance(observation.get("finish_readiness"), dict) else {}
    task_board = observation.get("task_board") if isinstance(observation.get("task_board"), dict) else {}
    tasks = task_board.get("tasks") if isinstance(task_board.get("tasks"), list) else []
    last_action_type = _last_action_type(loop)

    if state.terminal_status in {"completed", "failed", "cancelled"}:
        return LeadAction(
            type="fail",
            goal=f"Loop is already terminal: {state.terminal_status}.",
            agent="Lead",
            final_message=f"Lead loop already ended as {state.terminal_status}.",
        ).model_dump()

    if state.intent.execution_route == "lead_direct_reply":
        if last_action_type == "answer":
            return LeadAction(type="finish", goal="Finish after direct answer.", agent="Lead").model_dump()
        return LeadAction(
            type="answer",
            goal="Answer directly without creating tasks.",
            agent="Lead",
        ).model_dump()

    if state.intent.route == "clarification_needed":
        return LeadAction(
            type="ask_clarification",
            goal="Ask for missing task scope before executing.",
            agent="Lead",
            final_message="请补充要处理的对象、范围和验收标准。",
        ).model_dump()

    if readiness.get("ready"):
        if last_action_type == "summarize":
            return LeadAction(type="finish", goal="Finish after summary.", agent="Lead").model_dump()
        return LeadAction(
            type="summarize",
            goal="Summarize completed work before finishing.",
            agent="Lead",
        ).model_dump()

    counts = readiness.get("counts") if isinstance(readiness.get("counts"), dict) else {}
    if counts.get("failed") or counts.get("blocked") or counts.get("cancelled"):
        return LeadAction(
            type="fail",
            goal="Stop because the task board has failed, blocked, or cancelled work.",
            agent="Lead",
            context_requirements={"failed_task_ids": readiness.get("failed_task_ids", [])},
        ).model_dump()

    active_task = _first_task_with_status(tasks, {"running", "ready", "pending"})
    return LeadAction(
        type="inspect_project",
        goal="Observe task-board state and gather the next relevant context.",
        agent="Lead",
        task_id=active_task.get("id") if active_task else None,
        context_requirements={
            "task_status_counts": counts,
            "non_terminal_task_ids": readiness.get("non_terminal_task_ids", []),
        },
    ).model_dump()


def _last_action_type(loop: dict[str, Any]) -> str:
    steps = loop.get("steps") if isinstance(loop.get("steps"), list) else []
    if not steps:
        return ""
    action = steps[-1].get("action") if isinstance(steps[-1], dict) else {}
    return str(action.get("type") or "") if isinstance(action, dict) else ""


def _first_task_with_status(tasks: list[Any], statuses: set[str]) -> dict[str, Any] | None:
    for task in tasks:
        if isinstance(task, dict) and str(task.get("status") or "") in statuses:
            return task
    return None


def _phase_for_action(action_type: str) -> str:
    if action_type in {"answer", "ask_clarification", "create_tasks", "summarize"}:
        return "decide"
    if action_type in {"finish", "fail"}:
        return "final"
    return "act"


def _summary_for_action(action: dict[str, Any], *, repaired: bool) -> str:
    prefix = "Auto-repaired action. " if repaired else ""
    goal = str(action.get("goal") or "")
    action_type = str(action.get("type") or "action")
    return f"{prefix}{goal or action_type}"[:500]


def _execute_loop_tool_action(
    thread_id: str,
    workspace_dir: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    tool_call = action.get("tool_call") if isinstance(action.get("tool_call"), dict) else {}
    kind, target, payload = _action_request_from_tool_call(tool_call)
    if not kind:
        return {
            "executed": False,
            "result": "failure",
            "reason": "call_tool action 缺少可执行 tool 名称。",
            "tool_call": tool_call,
        }

    from src.api.services.action_execution_service import execute_action

    result = execute_action(
        kind=kind,
        target=target,
        payload=payload,
        thread_id=thread_id,
        workspace_dir=workspace_dir,
    )
    if result.get("requires_approval") and result.get("approval_id"):
        _append_pending_approval_step(thread_id, workspace_dir, kind, target, payload, result)
    return {
        "executed": True,
        "kind": kind,
        "target": target,
        "payload": payload,
        **result,
    }


def _action_request_from_tool_call(tool_call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    kind = str(tool_call.get("kind") or tool_call.get("tool") or "").strip()
    raw_input = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
    payload = dict(raw_input)
    payload.update(tool_call.get("payload") if isinstance(tool_call.get("payload"), dict) else {})
    target = str(tool_call.get("target") or payload.get("target") or "").strip()

    if kind in {"read_file", "write_file", "delete_file"}:
        target = target or str(payload.get("path") or payload.get("file") or "").strip()
    elif kind == "run_command":
        target = target or str(payload.get("command") or "").strip()
    elif kind == "mcp_call":
        target = target or str(payload.get("tool_name") or payload.get("tool") or "").strip()

    return kind, target, payload


def _append_pending_approval_step(
    thread_id: str,
    workspace_dir: str,
    kind: str,
    target: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> None:
    try:
        append_loop_step(
            thread_id,
            workspace_dir,
            action={
                "type": "request_approval",
                "goal": result.get("reason") or f"{kind} requires approval.",
                "agent": "Lead",
                "tool_call": {"tool": kind, "target": target, "input": payload},
                "approval": {
                    "approval_id": result.get("approval_id"),
                    "kind": kind,
                    "target": target,
                    "risk": result.get("risk"),
                },
            },
            phase="act",
            status="waiting",
            summary=f"Waiting for approval: {kind} {target}".strip(),
            pending_approval_id=str(result.get("approval_id") or ""),
        )
    except Exception:
        pass
