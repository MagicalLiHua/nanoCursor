"""Agent Loop controller evals.

These evals exercise the structured Lead loop without calling an LLM.  Each
case builds a small persisted run state, asks the controller for the next
action, and scores whether the proposed action is allowed and route-appropriate.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from src.api.services.agent_loop_controller_service import run_loop_controller_step
from src.api.services.agent_loop_state_service import append_loop_step, init_agent_loop_state
from src.api.services.eval_service import _evals_root, _workspace
from src.api.services.event_store import get_event_store
from src.api.services.intent_router import classify_user_intent
from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

AGENT_LOOP_CORE_EVALS: list[dict[str, Any]] = [
    {
        "id": "direct_answer_starts_with_answer",
        "prompt": "哈喽",
        "expected_route": "direct_answer",
        "expected_action": "answer",
        "expected_allowed": True,
        "must_not_write": True,
    },
    {
        "id": "direct_answer_finishes_after_answer",
        "prompt": "你好",
        "setup_steps": [{"type": "answer", "goal": "answer", "agent": "Lead"}],
        "expected_route": "direct_answer",
        "expected_action": "finish",
        "expected_allowed": True,
        "must_not_write": True,
    },
    {
        "id": "read_only_inspects_before_summary",
        "prompt": "帮我看看这个项目结构",
        "expected_route": "read_only",
        "expected_action": "inspect_project",
        "expected_allowed": True,
        "must_not_write": True,
    },
    {
        "id": "read_only_summarizes_after_inspection",
        "prompt": "帮我看看这个项目结构",
        "setup_steps": [{"type": "inspect_project", "goal": "inspect", "agent": "Lead"}],
        "expected_route": "read_only",
        "expected_action": "summarize",
        "expected_allowed": True,
        "must_not_write": True,
    },
    {
        "id": "test_only_runs_checks",
        "prompt": "帮我运行 pytest 验证一下",
        "tasks": [
            {"id": "tests", "type": "test", "title": "运行测试", "status": "ready", "agent_role": "tester"}
        ],
        "expected_route": "test_only",
        "expected_action": "run_checks",
        "expected_allowed": True,
        "must_not_write": True,
    },
    {
        "id": "feature_failed_task_creates_recovery",
        "prompt": "完整实现登录模块并补测试",
        "tasks": [
            {"id": "implementation", "type": "implementation", "title": "实现登录模块", "status": "failed"}
        ],
        "expected_route": "feature_delivery",
        "expected_action": "create_tasks",
        "expected_allowed": True,
        "must_have_context_key": "recovery",
    },
    {
        "id": "recent_tool_failure_creates_recovery",
        "prompt": "完整实现登录模块并补测试",
        "events": [
            {
                "type": "tool_call_failed",
                "title": "pytest failed",
                "content": "pytest failed",
                "agent": "tester",
                "payload": {"tool": "run_command", "ok": False, "status": "failed"},
            }
        ],
        "expected_route": "feature_delivery",
        "expected_action": "create_tasks",
        "expected_allowed": True,
        "must_have_context_key": "recovery",
    },
    {
        "id": "risky_operation_requests_approval",
        "prompt": "删除 old.py",
        "expected_route": "risky_operation",
        "expected_action": "request_approval",
        "expected_allowed": True,
        "must_have_approval": True,
    },
    {
        "id": "parallel_analysis_spawns_read_only_agent",
        "prompt": "完整实现登录模块并补测试",
        "tasks": [
            {
                "id": "impact-analysis",
                "type": "analysis",
                "title": "分析影响面",
                "status": "ready",
                "agent_role": "reviewer",
                "can_parallel": True,
            }
        ],
        "expected_route": "feature_delivery",
        "expected_action": "spawn_agent",
        "expected_allowed": True,
        "must_not_write": True,
        "must_have_context_key": "agent",
    },
]


def list_agent_loop_eval_cases() -> list[dict[str, Any]]:
    """Return Agent Loop controller eval cases."""

    return [dict(item) for item in AGENT_LOOP_CORE_EVALS]


def run_agent_loop_eval_suite(
    case_ids: list[str] | None = None,
    *,
    workspace_dir: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run selected Agent Loop controller eval cases."""

    workspace = _workspace(workspace_dir)
    catalog = {case["id"]: case for case in AGENT_LOOP_CORE_EVALS}
    selected_ids = case_ids or [case["id"] for case in AGENT_LOOP_CORE_EVALS]
    results: list[dict[str, Any]] = []
    for case_id in selected_ids:
        case = catalog.get(case_id)
        if not case:
            results.append({"id": case_id, "overall": "error", "error": "agent loop eval case not found"})
            continue
        results.append(run_agent_loop_eval_case(case, workspace))

    passed = sum(1 for item in results if item.get("overall") == "passed")
    failed = len(results) - passed
    failures = [item for item in results if item.get("overall") != "passed"]
    summary = {
        "suite": "agent_loop_core",
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(len(results), 1), 3),
        "failed_case_ids": [str(item.get("id") or "") for item in failures],
        "failures": failures,
        "metrics": _agent_loop_eval_metrics(results),
        "results": results,
        "completed_at": time.time(),
    }
    if persist:
        summary["eval_run_id"] = _persist_agent_loop_eval_result(summary, workspace)
    return summary


def run_agent_loop_eval_case(case: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Run one Agent Loop controller eval case in a sandbox workspace."""

    sandbox = _prepare_case_workspace(workspace, str(case.get("id") or "case"))
    thread_id = f"agent-loop-eval-{case.get('id')}"
    prompt = str(case.get("prompt") or "")
    intent = classify_user_intent(prompt)
    init_agent_loop_state(thread_id, str(sandbox), user_request=prompt, intent=intent)
    _apply_case_steps(case, thread_id, sandbox)
    _apply_case_tasks(case, thread_id, sandbox)
    _apply_case_events(case, thread_id, sandbox)

    result = run_loop_controller_step(thread_id, str(sandbox), commit=False)
    checks = _score_agent_loop_case(case, intent, result)
    overall = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "id": case.get("id"),
        "prompt": prompt,
        "overall": overall,
        "intent": intent,
        "selected_action": result.get("selected_action"),
        "check": result.get("check"),
        "checks": checks,
    }


def get_agent_loop_eval_run(eval_run_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read a persisted Agent Loop eval run."""

    result_path = _evals_root(_workspace(workspace_dir)) / "agent_loop" / _safe_id(eval_run_id) / "result.json"
    if not result_path.exists():
        raise ValueError(f"Agent Loop eval run not found: {eval_run_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _prepare_case_workspace(workspace: Path, case_id: str) -> Path:
    sandbox = _evals_root(workspace) / "agent_loop_sandbox" / _safe_id(case_id)
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "README.md").write_text("# Agent Loop Eval\n", encoding="utf-8")
    (sandbox / "old.py").write_text("print('old')\n", encoding="utf-8")
    return sandbox


def _apply_case_steps(case: dict[str, Any], thread_id: str, sandbox: Path) -> None:
    for action in case.get("setup_steps", []) if isinstance(case.get("setup_steps"), list) else []:
        if isinstance(action, dict):
            append_loop_step(
                thread_id,
                str(sandbox),
                action=action,
                phase="decide",
                status="completed",
                summary=str(action.get("goal") or action.get("type") or ""),
            )


def _apply_case_tasks(case: dict[str, Any], thread_id: str, sandbox: Path) -> None:
    raw_tasks = case.get("tasks") if isinstance(case.get("tasks"), list) else []
    if not raw_tasks:
        return
    tasks = [
        RunTask(
            id=str(item.get("id")),
            type=str(item.get("type") or "analysis"),  # type: ignore[arg-type]
            title=str(item.get("title") or item.get("id")),
            status=str(item.get("status") or "pending"),  # type: ignore[arg-type]
            agent_role=str(item.get("agent_role") or "lead"),
            can_parallel=bool(item.get("can_parallel", False)),
            writes_files=bool(item.get("writes_files", False)),
        )
        for item in raw_tasks
        if isinstance(item, dict)
    ]
    save_task_board(
        RunTaskBoard(run_id=thread_id, nodes=tasks),
        get_event_store().run_dir(thread_id, str(sandbox)),
    )


def _apply_case_events(case: dict[str, Any], thread_id: str, sandbox: Path) -> None:
    for event in case.get("events", []) if isinstance(case.get("events"), list) else []:
        if not isinstance(event, dict):
            continue
        get_event_store().append_event(
            thread_id,
            str(event.get("type") or "event"),
            title=str(event.get("title") or ""),
            content=str(event.get("content") or ""),
            agent=str(event.get("agent") or "lead"),
            payload=event.get("payload") if isinstance(event.get("payload"), dict) else {},
            workspace_dir=str(sandbox),
        )


def _score_agent_loop_case(case: dict[str, Any], intent: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    action = result.get("selected_action") if isinstance(result.get("selected_action"), dict) else {}
    check = result.get("check") if isinstance(result.get("check"), dict) else {}
    _expect(checks, "route", intent.get("route") == case.get("expected_route"), intent.get("route"), case.get("expected_route"))
    _expect(
        checks,
        "action_type",
        action.get("type") == case.get("expected_action"),
        action.get("type"),
        case.get("expected_action"),
    )
    if "expected_allowed" in case:
        _expect(
            checks,
            "allowed",
            bool(check.get("allowed")) is bool(case.get("expected_allowed")),
            check.get("allowed"),
            case.get("expected_allowed"),
        )
    if case.get("must_have_approval"):
        _expect(checks, "approval", bool(action.get("approval")), action.get("approval"), "non-empty")
    if case.get("must_have_context_key"):
        requirements = action.get("context_requirements") if isinstance(action.get("context_requirements"), dict) else {}
        key = str(case.get("must_have_context_key") or "")
        _expect(checks, f"context:{key}", key in requirements, sorted(requirements.keys()), key)
    if case.get("must_not_write"):
        _expect(
            checks,
            "no_write_action",
            action.get("type") not in {"call_tool"} or not _action_requests_write(action),
            action,
            "no write tool action",
        )
    return checks


def _action_requests_write(action: dict[str, Any]) -> bool:
    tool_call = action.get("tool_call") if isinstance(action.get("tool_call"), dict) else {}
    name = str(tool_call.get("tool") or tool_call.get("kind") or "")
    return name in {"write_file", "delete_file", "replace_file", "run_command", "mcp_call"}


def _agent_loop_eval_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_action: dict[str, dict[str, int]] = {}
    for item in results:
        action = item.get("selected_action") if isinstance(item.get("selected_action"), dict) else {}
        action_type = str(action.get("type") or "unknown")
        stat = by_action.setdefault(action_type, {"total": 0, "passed": 0, "failed": 0})
        stat["total"] += 1
        if item.get("overall") == "passed":
            stat["passed"] += 1
        else:
            stat["failed"] += 1
    return {"actions": by_action}


def _persist_agent_loop_eval_result(summary: dict[str, Any], workspace: Path) -> str:
    eval_run_id = f"agent-loop-core-{int(time.time() * 1000)}"
    result_dir = _evals_root(workspace) / "agent_loop" / eval_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return eval_run_id


def _expect(
    checks: list[dict[str, Any]],
    check_id: str,
    condition: bool,
    actual: Any,
    expected: Any,
) -> None:
    checks.append({
        "id": check_id,
        "status": "passed" if condition else "failed",
        "actual": actual,
        "expected": expected,
    })


def _safe_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value)).strip("-")
    return safe or "run"
