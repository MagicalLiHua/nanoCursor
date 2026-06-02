"""Intent routing evals for Intent Router V3."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.api.services.eval_service import _evals_root, _workspace
from src.api.services.intent_router import classify_user_intent


INTENT_CORE_EVALS: list[dict[str, Any]] = [
    {
        "id": "greeting_direct_answer",
        "prompt": "你好",
        "expected_route": "direct_answer",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
        "forbidden_agents": ["Coder", "Tester"],
        "notes": "问候不应生成任务卡。",
    },
    {
        "id": "identity_direct_answer",
        "prompt": "你是什么模型",
        "expected_route": "direct_answer",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
        "forbidden_agents": ["Coder", "Tester"],
    },
    {
        "id": "general_explanation_direct_answer",
        "prompt": "解释一下快速排序为什么平均复杂度是 nlogn",
        "expected_route": "direct_answer",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
    },
    {
        "id": "workspace_structure_read_only",
        "prompt": "帮我看看这个项目结构",
        "expected_route": "read_only",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": False,
        "forbidden_agents": ["Coder"],
    },
    {
        "id": "readme_typo_small_edit",
        "prompt": "帮我改 README 的错别字",
        "expected_route": "small_edit",
        "expected_complexity": "small_code",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_approval": False,
    },
    {
        "id": "python_script_feature_delivery",
        "prompt": "帮我用 Python 写常见排序算法并比较性能",
        "expected_route": "feature_delivery",
        "expected_complexity": "small_code",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": False,
    },
    {
        "id": "bug_traceback_debug_fix",
        "prompt": "运行时报错 Traceback: ModuleNotFoundError，请帮我修复",
        "expected_route": "debug_fix",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_shell": True,
    },
    {
        "id": "pytest_test_only",
        "prompt": "帮我运行 pytest 验证一下",
        "expected_route": "test_only",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": False,
        "must_require_shell": True,
    },
    {
        "id": "diff_review_only",
        "prompt": "帮我审查一下当前 diff 有没有风险",
        "expected_route": "review_only",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": False,
    },
    {
        "id": "delete_files_risky_operation",
        "prompt": "帮我删除 node_modules",
        "expected_route": "risky_operation",
        "expected_complexity": "high_risk",
        "expected_execution_route": "agenthub_delivery",
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": True,
    },
    {
        "id": "install_dependency_risky_operation",
        "prompt": "帮我安装依赖并运行 npm install",
        "expected_route": "risky_operation",
        "expected_complexity": "high_risk",
        "expected_execution_route": "agenthub_delivery",
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": True,
    },
    {
        "id": "ambiguous_improve_clarification",
        "prompt": "帮我优化一下",
        "expected_route": "clarification_needed",
        "expected_complexity": "simple",
        "expected_execution_route": "lead_direct_reply",
        "must_require_write": False,
        "must_have_missing_information": True,
    },
    {
        "id": "conversation_followup_uses_memory",
        "prompt": "继续",
        "conversation_summary": "上一轮用户要求实现 Python 排序算法性能比较脚本，已经进入代码任务并准备写文件。",
        "expected_route": "feature_delivery",
        "expected_complexity": "small_code",
        "expected_execution_route": "agenthub_delivery",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_approval": False,
        "notes": "短 follow-up 必须参考 conversation memory，不能按普通闲聊处理。",
    },
]


def list_intent_eval_cases() -> list[dict[str, Any]]:
    """Return the core intent-routing eval catalog."""
    return [dict(item) for item in INTENT_CORE_EVALS]


def run_intent_eval_suite(
    case_ids: list[str] | None = None,
    *,
    workspace_dir: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run the selected intent eval cases and optionally persist the result."""
    catalog = {case["id"]: case for case in INTENT_CORE_EVALS}
    selected_ids = case_ids or [case["id"] for case in INTENT_CORE_EVALS]
    results: list[dict[str, Any]] = []
    for case_id in selected_ids:
        case = catalog.get(case_id)
        if not case:
            results.append({"id": case_id, "overall": "error", "error": "intent eval case not found"})
            continue
        results.append(run_intent_eval_case(case))

    passed = sum(1 for item in results if item.get("overall") == "passed")
    failed = len(results) - passed
    summary = {
        "suite": "intent_core",
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(len(results), 1), 3),
        "results": results,
        "completed_at": time.time(),
    }
    if persist:
        summary["eval_run_id"] = _persist_intent_eval_result(summary, workspace_dir)
    return summary


def run_intent_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one intent eval case."""
    decision = classify_user_intent(
        str(case.get("prompt") or ""),
        conversation_summary=str(case.get("conversation_summary") or ""),
    )
    checks = _score_intent_decision(case, decision)
    overall = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "id": case.get("id"),
        "prompt": case.get("prompt"),
        "overall": overall,
        "decision": decision,
        "checks": checks,
    }


def _score_intent_decision(case: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _expect(checks, "route", decision.get("route") == case.get("expected_route"), decision.get("route"), case.get("expected_route"))
    if case.get("expected_complexity"):
        _expect(
            checks,
            "complexity",
            decision.get("complexity") == case.get("expected_complexity"),
            decision.get("complexity"),
            case.get("expected_complexity"),
        )
    if case.get("expected_execution_route"):
        _expect(
            checks,
            "execution_route",
            decision.get("execution_route") == case.get("expected_execution_route"),
            decision.get("execution_route"),
            case.get("expected_execution_route"),
        )
    for key, field in [
        ("must_require_read", "requires_workspace_read"),
        ("must_require_write", "requires_workspace_write"),
        ("must_require_shell", "requires_shell"),
        ("must_require_approval", "requires_approval"),
    ]:
        if key in case:
            _expect(checks, field, bool(decision.get(field)) is bool(case.get(key)), decision.get(field), case.get(key))
    if case.get("must_have_missing_information"):
        _expect(checks, "missing_information", bool(decision.get("missing_information")), decision.get("missing_information"), "non-empty")
    forbidden_agents = {str(agent).lower() for agent in case.get("forbidden_agents", [])}
    if forbidden_agents:
        agents = {str(agent).lower() for agent in decision.get("suggested_agents", [])}
        _expect(
            checks,
            "forbidden_agents",
            not bool(agents & forbidden_agents),
            sorted(agents),
            f"no {sorted(forbidden_agents)}",
        )
    _expect(checks, "v3_context_requirements", isinstance(decision.get("context_requirements"), dict), type(decision.get("context_requirements")).__name__, "dict")
    _expect(checks, "v3_tool_permissions", isinstance(decision.get("tool_permissions"), dict), type(decision.get("tool_permissions")).__name__, "dict")
    _expect(checks, "v3_agent_specs", isinstance(decision.get("suggested_agent_specs"), list), type(decision.get("suggested_agent_specs")).__name__, "list")
    return checks


def _expect(checks: list[dict[str, Any]], check_id: str, ok: bool, actual: Any, expected: Any) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "passed" if ok else "failed",
            "actual": actual,
            "expected": expected,
        }
    )


def _persist_intent_eval_result(summary: dict[str, Any], workspace_dir: str | None) -> str:
    workspace = _workspace(workspace_dir)
    eval_run_id = f"intent-core-{int(time.time() * 1000)}"
    result_dir = _evals_root(workspace) / "intent" / eval_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return eval_run_id


def get_intent_eval_run(eval_run_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read a persisted intent eval run."""
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in eval_run_id).strip("-")
    result_path = _evals_root(_workspace(workspace_dir)) / "intent" / safe_id / "result.json"
    if not result_path.exists():
        raise ValueError(f"Intent eval run 不存在: {eval_run_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))
