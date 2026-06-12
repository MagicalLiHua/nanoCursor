"""Finish-readiness assessment for the Agent Loop.

This module is the Lead loop's completion gate.  It does not execute work; it
only inspects durable evidence and explains whether a run may safely finish.
"""

from __future__ import annotations

from typing import Any

from src.api.services.event_store import get_event_store

SUCCESS_TASK_STATUSES = {"passed", "skipped"}
FAILED_TASK_STATUSES = {"failed", "blocked", "cancelled"}
WRITE_ROUTES = {"small_edit", "feature_delivery", "debug_fix", "risky_operation"}
TASK_REQUIRED_ROUTES = {"feature_delivery", "debug_fix", "test_only", "risky_operation"}
TEST_REQUIRED_ROUTES = {"feature_delivery", "debug_fix", "test_only"}
WRITE_TOOLS = {
    "write_file",
    "edit_file",
    "delete_file",
    "move_file",
    "replace_file",
    "apply_patch",
}
TEST_TOOLS = {"run_tests", "pytest", "bash", "run_command", "shell"}
TEST_COMMAND_MARKERS = (
    "pytest",
    "npm test",
    "pnpm test",
    "yarn test",
    "go test",
    "ruff",
    "mypy",
    "vitest",
    "jest",
    "lint",
)


def build_loop_finish_readiness(
    thread_id: str,
    workspace_dir: str,
    *,
    state: Any | None = None,
) -> dict[str, Any]:
    """Return a durable, explainable completion assessment for one run."""
    session = get_event_store().get_session(thread_id, workspace_dir) or {}
    route = _state_attr(state, "intent.route") or _session_intent_attr(session, "route")
    execution_route = _state_attr(state, "intent.execution_route") or _session_intent_attr(
        session,
        "execution_route",
    )
    terminal_status = _state_attr(state, "terminal_status")
    pending_approval_id = _state_attr(state, "pending_approval_id")

    events = get_event_store().list_events(thread_id, workspace_dir)
    event_evidence = _collect_event_evidence(events)
    board = _load_task_board(thread_id, workspace_dir)
    task_evidence = _collect_task_evidence(board)
    evidence = {
        **event_evidence,
        **task_evidence,
        "route": route,
        "execution_route": execution_route,
    }

    if terminal_status == "waiting_approval" or pending_approval_id:
        return _result(
            False,
            "approval_wait",
            "存在待审批动作，不能完成本轮 Agent Loop。",
            evidence=evidence,
            required_actions=["resolve_approval"],
        )

    if route == "direct_answer" or execution_route == "lead_direct_reply":
        return _result(
            True,
            "direct_answer",
            "Lead direct reply does not require task-board or tool evidence.",
            evidence=evidence,
        )

    if task_evidence["task_count"] == 0:
        if route in {"read_only", "review_only"}:
            return _result(
                True,
                "read_only_no_task_board",
                "Read-only or review runs may finish without a task board after inspection.",
                evidence=evidence,
            )
        if route in WRITE_ROUTES:
            if event_evidence["has_write_evidence"] and event_evidence["has_diff_evidence"]:
                warnings = []
                if route in TEST_REQUIRED_ROUTES and not event_evidence["has_test_evidence"]:
                    warnings.append("缺少测试或检查证据。")
                return _result(
                    True,
                    "write_evidence",
                    "Detected write and Diff evidence without a persisted task board.",
                    evidence=evidence,
                    warnings=warnings,
                )
            return _result(
                False,
                "missing_write_evidence",
                "写入类任务缺少任务板或真实写入证据，不能完成。",
                evidence=evidence,
                required_actions=["create_tasks" if route in TASK_REQUIRED_ROUTES else "call_tool"],
            )
        if route == "test_only":
            return _result(
                False,
                "missing_test_evidence",
                "测试类任务缺少任务板或测试证据，不能完成。",
                evidence=evidence,
                required_actions=["run_checks"],
            )
        return _result(
            True,
            "no_task_board",
            "No persisted task board exists for this run.",
            evidence=evidence,
        )

    if task_evidence["failed_task_ids"]:
        return _result(
            False,
            "task_board_failed",
            "Task board has failed, blocked, or cancelled work.",
            evidence=evidence,
            required_actions=["classify_failure", "recover_or_retry"],
        )

    if task_evidence["non_terminal_task_ids"]:
        return _result(
            False,
            "task_board_unfinished",
            "Task board still has unfinished work.",
            evidence=evidence,
            required_actions=["continue_tasks"],
        )

    warnings: list[str] = []
    if route in WRITE_ROUTES and not event_evidence["has_write_evidence"]:
        return _result(
            False,
            "missing_write_evidence",
            "任务板已完成，但缺少本轮真实写入证据。",
            evidence=evidence,
            required_actions=["inspect_diff", "call_tool"],
        )

    if route in WRITE_ROUTES and not event_evidence["has_diff_evidence"]:
        return _result(
            False,
            "missing_diff_evidence",
            "任务板已完成，但缺少 Diff 或文件变更证据。",
            evidence=evidence,
            required_actions=["inspect_diff"],
        )

    if route in TEST_REQUIRED_ROUTES and not event_evidence["has_test_evidence"]:
        return _result(
            False,
            "missing_test_evidence",
            "任务板已完成，但缺少测试或检查证据。",
            evidence=evidence,
            required_actions=["run_checks"],
        )

    if event_evidence["quality_status"] in {"failed", "error"}:
        return _result(
            False,
            "quality_gate_failed",
            "质量门禁未通过，不能完成。",
            evidence=evidence,
            required_actions=["fix_quality_gate"],
        )

    if route in WRITE_ROUTES and not event_evidence["has_quality_evidence"]:
        warnings.append("缺少质量门禁事件，已按任务和工具证据允许收口。")

    return _result(
        True,
        "task_board_and_evidence",
        "Task board and required runtime evidence are complete.",
        evidence=evidence,
        warnings=warnings,
    )


def _load_task_board(thread_id: str, workspace_dir: str) -> Any | None:
    try:
        from src.runtime.task_board import load_task_board

        return load_task_board(get_event_store().run_dir(thread_id, workspace_dir))
    except Exception:
        return None


def _collect_task_evidence(board: Any | None) -> dict[str, Any]:
    nodes = list(getattr(board, "nodes", []) or [])
    counts: dict[str, int] = {}
    for task in nodes:
        status = str(getattr(task, "status", "") or "")
        counts[status] = counts.get(status, 0) + 1
    non_terminal = [
        task
        for task in nodes
        if str(getattr(task, "status", "") or "") not in SUCCESS_TASK_STATUSES
    ]
    failed = [
        task
        for task in nodes
        if str(getattr(task, "status", "") or "") in FAILED_TASK_STATUSES
    ]
    return {
        "task_count": len(nodes),
        "counts": counts,
        "non_terminal_task_ids": [str(getattr(task, "id", "")) for task in non_terminal],
        "failed_task_ids": [str(getattr(task, "id", "")) for task in failed],
    }


def _collect_event_evidence(events: list[Any]) -> dict[str, Any]:
    write_events: list[dict[str, Any]] = []
    diff_events: list[dict[str, Any]] = []
    test_events: list[dict[str, Any]] = []
    quality_events: list[dict[str, Any]] = []
    last_quality_status = ""
    for event in events:
        event_type = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        if event_type == "file_changed" or _is_successful_write_tool(event_type, payload):
            write_events.append(_event_ref(event))
        if event_type == "diff_updated" or _payload_has_changed_files(payload):
            diff_events.append(_event_ref(event))
        if event_type == "test_finished" or _is_test_tool(event_type, payload):
            test_events.append(_event_ref(event))
        if event_type in {"quality_gate", "delivery_scored", "runtime_delivery_evidence"}:
            quality_events.append(_event_ref(event))
            status = str(payload.get("status") or payload.get("result") or "").lower()
            if not status and payload.get("ready") is False:
                status = "failed"
            if status:
                last_quality_status = status
    return {
        "event_count": len(events),
        "has_write_evidence": bool(write_events),
        "has_diff_evidence": bool(diff_events),
        "has_test_evidence": bool(test_events),
        "has_quality_evidence": bool(quality_events),
        "quality_status": last_quality_status,
        "write_events": write_events[-5:],
        "diff_events": diff_events[-5:],
        "test_events": test_events[-5:],
        "quality_events": quality_events[-5:],
    }


def _is_successful_write_tool(event_type: str, payload: dict[str, Any]) -> bool:
    if event_type != "tool_call_finished":
        return False
    tool = str(payload.get("tool") or payload.get("name") or "").lower()
    ok = payload.get("ok", payload.get("success", True))
    return tool in WRITE_TOOLS and ok is not False


def _payload_has_changed_files(payload: dict[str, Any]) -> bool:
    changed = payload.get("changed_files")
    return isinstance(changed, list) and bool(changed)


def _is_test_tool(event_type: str, payload: dict[str, Any]) -> bool:
    if event_type != "tool_call_finished":
        return False
    tool = str(payload.get("tool") or payload.get("name") or "").lower()
    if tool not in TEST_TOOLS:
        return False
    input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    command = str(input_data.get("command") or payload.get("command") or "").lower()
    if tool == "run_tests":
        return payload.get("ok", True) is not False
    return any(marker in command for marker in TEST_COMMAND_MARKERS) and payload.get("ok", True) is not False


def _event_ref(event: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(event, "id", "") or ""),
        "type": str(getattr(event, "type", "") or ""),
        "title": str(getattr(event, "title", "") or ""),
        "agent": str(getattr(event, "agent", "") or ""),
    }


def _result(
    ready: bool,
    mode: str,
    reason: str,
    *,
    evidence: dict[str, Any],
    required_actions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ready": ready,
        "mode": mode,
        "reason": reason,
        "task_count": evidence.get("task_count", 0),
        "counts": evidence.get("counts", {}),
        "non_terminal_task_ids": evidence.get("non_terminal_task_ids", []),
        "failed_task_ids": evidence.get("failed_task_ids", []),
        "required_actions": required_actions or [],
        "warnings": warnings or [],
        "evidence": evidence,
    }


def _state_attr(state: Any | None, path: str) -> str:
    if state is None:
        return ""
    value: Any = state
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
        if value is None:
            return ""
    return str(value or "")


def _session_intent_attr(session: dict[str, Any], key: str) -> str:
    intent = session.get("intent_decision") if isinstance(session.get("intent_decision"), dict) else {}
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    plan_intent = plan.get("intent_decision") if isinstance(plan.get("intent_decision"), dict) else {}
    return str(intent.get(key) or plan_intent.get(key) or "")
