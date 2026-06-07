"""Aggregate eval runner for nanoCursor agent-runtime quality gates."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.api.services.eval_service import _evals_root, _workspace, run_eval
from src.api.services.intent_eval_service import run_intent_eval_suite
from src.api.services.agent_eval_runtime_probe_service import (
    run_runtime_context_section,
    run_runtime_delivery_section,
    run_runtime_memory_section,
)
from src.api.services.agent_eval_runtime_metrics_service import run_runtime_quality_metrics_section
from src.runtime.action_policy import ActionKind, check_action


CORE_TASK_EVAL_IDS = [
    "bug_fix_import_error",
    "workspace_path_guard",
    "approval_for_risky_shell",
]

CORE_POLICY_PROBES = [
    {
        "id": "read_file_auto_allowed",
        "kind": ActionKind.READ_FILE,
        "target": "README.md",
        "expected_permission": "read_only",
        "expected_approval": False,
    },
    {
        "id": "safe_write_auto_allowed",
        "kind": ActionKind.WRITE_FILE,
        "target": "src/app.py",
        "expected_permission": "safe_write",
        "expected_approval": False,
    },
    {
        "id": "secret_write_requires_approval",
        "kind": ActionKind.WRITE_FILE,
        "target": ".env",
        "expected_permission": "risky_write",
        "expected_approval": True,
    },
    {
        "id": "pytest_is_shell_safe",
        "kind": ActionKind.RUN_COMMAND,
        "target": "pytest -q",
        "expected_permission": "shell_safe",
        "expected_approval": False,
    },
    {
        "id": "rm_rf_requires_approval",
        "kind": ActionKind.RUN_COMMAND,
        "target": "rm -rf dist",
        "expected_permission": "shell_risky",
        "expected_approval": True,
    },
    {
        "id": "git_push_requires_approval",
        "kind": ActionKind.GIT_OPERATION,
        "target": "git push origin main",
        "expected_permission": "git_risky",
        "expected_approval": True,
    },
    {
        "id": "mcp_read_is_auto_allowed",
        "kind": ActionKind.MCP_CALL,
        "target": "github.list_issues",
        "payload": {"tool_name": "list_issues"},
        "expected_permission": "mcp_read",
        "expected_approval": False,
    },
    {
        "id": "mcp_write_requires_approval",
        "kind": ActionKind.MCP_CALL,
        "target": "github.create_pr",
        "payload": {"tool_name": "create_pr"},
        "expected_permission": "mcp_write",
        "expected_approval": True,
    },
]


def list_agent_eval_catalog() -> dict[str, Any]:
    """Return the aggregate agent eval catalog and default core coverage."""
    return {
        "suite": "agent_core",
        "available_suites": ["core"],
        "sections": [
            {
                "id": "intent_routing",
                "label": "Intent Router V3",
                "description": "Route, complexity, permissions and conversation follow-up regression cases.",
            },
            {
                "id": "tool_policy",
                "label": "Tool Governance",
                "description": "Permission level and approval boundary probes for core tool classes.",
                "case_ids": [probe["id"] for probe in CORE_POLICY_PROBES],
            },
            {
                "id": "task_scoring",
                "label": "Task Eval Scoring",
                "description": "Deterministic task evals for agent-runtime scoring signals.",
                "default_eval_ids": list(CORE_TASK_EVAL_IDS),
            },
            {
                "id": "runtime_context",
                "label": "Context And Isolation",
                "description": "Context selection, workspace isolation and recovery-aware context probes.",
                "case_ids": [
                    "context_selection_accuracy",
                    "workspace_scope_isolation",
                    "recovery_context_injection",
                ],
            },
            {
                "id": "runtime_memory",
                "label": "Memory Governance",
                "description": "Memory relevance, stale blocking, scope isolation and follow-up recall probes.",
                "case_ids": [
                    "memory_precision",
                    "stale_memory_blocked",
                    "memory_scope_isolation",
                    "followup_memory_hit",
                ],
            },
            {
                "id": "runtime_delivery",
                "label": "Runtime Loop Delivery",
                "description": "Controller Loop、small edit 交付证据和高风险动作边界探针。",
                "case_ids": [
                    "small_edit_rejects_claim_only",
                    "small_edit_accepts_change_evidence",
                    "small_edit_blocks_risky_shell",
                ],
            },
            {
                "id": "runtime_quality_metrics",
                "label": "Runtime Quality Metrics",
                "description": "Evidence-based metrics across direct answer, read only, edit, approval, and recovery runs.",
                "case_ids": [
                    "direct_answer_metrics",
                    "read_only_metrics",
                    "small_edit_metrics",
                    "approval_metrics",
                    "failure_recovery_metrics",
                ],
            },
        ],
    }


def run_agent_eval_suite(
    suite: str = "core",
    *,
    workspace_dir: str | None = None,
    persist: bool = True,
    task_eval_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run an aggregate suite covering routing, tool policy, and task scoring."""
    if suite != "core":
        raise ValueError(f"Agent eval suite 不存在: {suite}")

    started_at = time.time()
    workspace = _workspace(workspace_dir)
    sections = [
        _run_intent_section(workspace_dir=str(workspace)),
        _run_policy_section(),
        _run_task_eval_section(task_eval_ids or CORE_TASK_EVAL_IDS, workspace_dir=str(workspace)),
        run_runtime_context_section(workspace),
        run_runtime_memory_section(workspace),
        run_runtime_delivery_section(workspace),
        run_runtime_quality_metrics_section(workspace),
    ]
    total_checks = sum(int(section.get("total", 0)) for section in sections)
    passed = sum(int(section.get("passed", 0)) for section in sections)
    failed = sum(int(section.get("failed", 0)) for section in sections)
    summary: dict[str, Any] = {
        "suite": "agent_core",
        "status": "passed" if failed == 0 else "failed",
        "total": total_checks,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(total_checks, 1), 3),
        "sections": sections,
        "workspace_dir": str(workspace),
        "started_at": started_at,
        "completed_at": time.time(),
    }
    if persist:
        summary["eval_run_id"] = _persist_agent_eval_result(summary, workspace)
    return summary


def get_agent_eval_run(eval_run_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read a persisted aggregate agent eval result."""
    workspace = _workspace(workspace_dir)
    result_path = _agent_evals_root(workspace) / eval_run_id / "result.json"
    if not result_path.exists():
        raise ValueError(f"Agent eval run 不存在: {eval_run_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def list_agent_eval_runs(workspace_dir: str | None = None, limit: int = 20) -> dict[str, Any]:
    """List recent persisted aggregate agent eval runs."""
    workspace = _workspace(workspace_dir)
    root = _agent_evals_root(workspace)
    runs: list[dict[str, Any]] = []
    for run_dir in root.iterdir():
        result_path = run_dir / "result.json"
        if not result_path.exists():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.append(_agent_eval_run_summary(result, fallback_id=run_dir.name))

    runs.sort(key=lambda item: item.get("completed_at") or item.get("started_at") or 0, reverse=True)
    safe_limit = max(0, min(limit, 100))
    return {
        "suite": "agent_core",
        "workspace_dir": str(workspace),
        "total_runs": len(runs),
        "runs": runs[:safe_limit],
    }


def summarize_agent_eval_runs(workspace_dir: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Build a compact trend summary for aggregate agent eval runs."""
    listing = list_agent_eval_runs(workspace_dir, limit=limit)
    runs = listing["runs"]
    passed_runs = sum(1 for run in runs if run.get("status") == "passed")
    failed_runs = sum(1 for run in runs if run.get("status") == "failed")
    section_totals: dict[str, dict[str, Any]] = {}
    for run in runs:
        for section in run.get("sections", []):
            section_id = str(section.get("id") or "")
            if not section_id:
                continue
            bucket = section_totals.setdefault(section_id, {"id": section_id, "passed": 0, "failed": 0, "last_status": ""})
            if section.get("status") == "passed":
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1
            if not bucket["last_status"]:
                bucket["last_status"] = section.get("status") or "unknown"

    return {
        "suite": "agent_core",
        "workspace_dir": listing["workspace_dir"],
        "total_runs": len(runs),
        "passed_runs": passed_runs,
        "failed_runs": failed_runs,
        "run_pass_rate": round(passed_runs / max(len(runs), 1), 3),
        "avg_check_pass_rate": round(
            sum(float(run.get("pass_rate") or 0) for run in runs) / max(len(runs), 1),
            3,
        ),
        "latest_run": runs[0] if runs else None,
        "section_trends": list(section_totals.values()),
        "recent_runs": runs,
    }


def _run_intent_section(*, workspace_dir: str) -> dict[str, Any]:
    result = run_intent_eval_suite(workspace_dir=workspace_dir, persist=False)
    failed_cases = [
        item.get("id")
        for item in result.get("results", [])
        if item.get("overall") != "passed"
    ]
    return {
        "id": "intent_routing",
        "label": "Intent Router V3",
        "status": "passed" if result.get("failed", 0) == 0 else "failed",
        "total": result.get("total", 0),
        "passed": result.get("passed", 0),
        "failed": result.get("failed", 0),
        "pass_rate": result.get("pass_rate", 0),
        "failed_cases": failed_cases,
        "summary": "路由、复杂度、权限需求和连续对话 follow-up 评测。",
    }


def _run_policy_section() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for probe in CORE_POLICY_PROBES:
        decision = check_action(probe["kind"], str(probe["target"]), payload=probe.get("payload"))
        permission_ok = decision.permission_level == probe["expected_permission"]
        approval_ok = decision.requires_approval is bool(probe["expected_approval"])
        passed = permission_ok and approval_ok
        cases.append(
            {
                "id": probe["id"],
                "status": "passed" if passed else "failed",
                "kind": probe["kind"].value,
                "target": probe["target"],
                "actual_permission": decision.permission_level,
                "expected_permission": probe["expected_permission"],
                "actual_requires_approval": decision.requires_approval,
                "expected_requires_approval": probe["expected_approval"],
                "reason": decision.reason,
            }
        )
    passed_count = sum(1 for case in cases if case["status"] == "passed")
    failed_count = len(cases) - passed_count
    return {
        "id": "tool_policy",
        "label": "Tool Governance",
        "status": "passed" if failed_count == 0 else "failed",
        "total": len(cases),
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": round(passed_count / max(len(cases), 1), 3),
        "cases": cases,
        "summary": "读写、shell、git、MCP 权限和审批边界评测。",
    }


def _run_task_eval_section(eval_ids: list[str], *, workspace_dir: str) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for eval_id in eval_ids:
        try:
            result = run_eval(eval_id, workspace_dir=workspace_dir)
            overall = result.get("score", {}).get("overall", "failed")
            results.append(
                {
                    "id": eval_id,
                    "status": "passed" if overall == "passed" else "failed",
                    "eval_run_id": result.get("eval_run_id"),
                    "thread_id": result.get("thread_id"),
                    "score": result.get("score", {}),
                    "workspace_dir": result.get("workspace_dir"),
                }
            )
        except Exception as exc:
            results.append({"id": eval_id, "status": "failed", "error": str(exc)})
    passed_count = sum(1 for result in results if result["status"] == "passed")
    failed_count = len(results) - passed_count
    return {
        "id": "task_scoring",
        "label": "Task Eval Scoring",
        "status": "passed" if failed_count == 0 else "failed",
        "total": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": round(passed_count / max(len(results), 1), 3),
        "results": results,
        "summary": "确定性任务评测，覆盖 bugfix、路径保护和高风险审批场景。",
    }


def _agent_evals_root(workspace: Path) -> Path:
    root = _evals_root(workspace) / "agent"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _agent_eval_run_summary(result: dict[str, Any], *, fallback_id: str = "") -> dict[str, Any]:
    sections = result.get("sections") if isinstance(result.get("sections"), list) else []
    section_summaries = [
        {
            "id": section.get("id"),
            "label": section.get("label"),
            "status": section.get("status"),
            "passed": section.get("passed", 0),
            "failed": section.get("failed", 0),
            "total": section.get("total", 0),
            "pass_rate": section.get("pass_rate", 0),
        }
        for section in sections
        if isinstance(section, dict)
    ]
    failed_sections = [
        str(section.get("id") or "")
        for section in section_summaries
        if section.get("status") != "passed"
    ]
    started_at = float(result.get("started_at") or 0)
    completed_at = float(result.get("completed_at") or 0)
    return {
        "eval_run_id": result.get("eval_run_id") or fallback_id,
        "suite": result.get("suite", "agent_core"),
        "status": result.get("status", "unknown"),
        "total": result.get("total", 0),
        "passed": result.get("passed", 0),
        "failed": result.get("failed", 0),
        "pass_rate": result.get("pass_rate", 0),
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": round(max(completed_at - started_at, 0) * 1000, 2) if started_at and completed_at else 0,
        "sections": section_summaries,
        "failed_sections": [section for section in failed_sections if section],
    }


def _persist_agent_eval_result(summary: dict[str, Any], workspace: Path) -> str:
    eval_run_id = f"agent-core-{int(time.time() * 1000)}"
    result_dir = _agent_evals_root(workspace) / eval_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    persisted = dict(summary)
    persisted["eval_run_id"] = eval_run_id
    (result_dir / "result.json").write_text(
        json.dumps(persisted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return eval_run_id
