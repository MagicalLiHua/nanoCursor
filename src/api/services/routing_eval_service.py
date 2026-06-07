"""Routing Decision evals for Agent Loop scheduling boundaries."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from src.api.services.eval_service import _evals_root, _workspace
from src.api.services.intent_router import classify_user_intent
from src.api.services.routing_decision_service import build_routing_decision
from src.api.services.skill_registry_service import import_skill


ROUTING_CORE_EVALS: list[dict[str, Any]] = [
    {
        "id": "greeting_lead_only",
        "prompt": "你好",
        "expected_route": "direct_answer",
        "expected_execution_route": "lead_direct_reply",
        "expected_next_action": "answer_directly",
        "must_require_write": False,
        "must_require_execution": False,
        "forbidden_agents": ["Coder", "Tester", "Reviewer"],
        "expected_agent_count": 1,
        "forbid_skills": True,
        "forbid_mcp": True,
    },
    {
        "id": "project_question_read_only",
        "prompt": "解释一下这个项目",
        "expected_route": "read_only",
        "expected_execution_route": "agenthub_delivery",
        "expected_next_action": "inspect_files",
        "must_require_read": True,
        "must_require_write": False,
        "forbidden_agents": ["Coder", "Tester"],
    },
    {
        "id": "single_file_small_code_edit",
        "prompt": "给 utils.py 加一个函数",
        "expected_route": "small_edit",
        "expected_execution_route": "agenthub_delivery",
        "expected_next_action": "edit_with_lead",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_shell": False,
        "required_agents": ["Coder"],
        "forbidden_agents": ["Tester"],
    },
    {
        "id": "code_edit_with_tests",
        "prompt": "给 utils.py 加一个函数并补 pytest",
        "expected_route": "feature_delivery",
        "expected_execution_route": "agenthub_delivery",
        "expected_next_action": "create_agents",
        "must_require_read": True,
        "must_require_write": True,
        "must_require_shell": True,
        "required_agents": ["Coder", "Tester"],
    },
    {
        "id": "delete_directory_requires_approval",
        "prompt": "删除整个目录",
        "expected_route": "risky_operation",
        "expected_next_action": "request_approval",
        "expected_risk": "high",
        "must_require_write": True,
        "must_require_shell": True,
        "must_require_approval": True,
    },
    {
        "id": "github_issue_selects_mcp",
        "prompt": "用 GitHub issue 信息分析这个需求",
        "expected_route": "read_only",
        "expected_next_action": "select_mcp_tools",
        "must_require_read": True,
        "must_require_write": False,
        "expected_mcp_servers": ["mcp.github"],
    },
    {
        "id": "python_refactor_selects_skill",
        "prompt": "按 Python 项目规范重构",
        "setup_skills": ["python-dev"],
        "expected_route": "feature_delivery",
        "expected_next_action": "create_agents",
        "required_agents": ["Coder", "Reviewer"],
        "expected_skills": ["skill.python-dev"],
    },
]


def list_routing_eval_cases() -> list[dict[str, Any]]:
    """Return Routing Decision eval cases."""
    return [dict(item) for item in ROUTING_CORE_EVALS]


def run_routing_eval_suite(
    case_ids: list[str] | None = None,
    *,
    workspace_dir: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run selected Routing Decision eval cases."""
    workspace = _workspace(workspace_dir)
    catalog = {case["id"]: case for case in ROUTING_CORE_EVALS}
    selected_ids = case_ids or [case["id"] for case in ROUTING_CORE_EVALS]
    results: list[dict[str, Any]] = []
    for case_id in selected_ids:
        case = catalog.get(case_id)
        if not case:
            results.append({"id": case_id, "overall": "error", "error": "routing eval case not found"})
            continue
        results.append(run_routing_eval_case(case, workspace))

    passed = sum(1 for item in results if item.get("overall") == "passed")
    failed = len(results) - passed
    summary = {
        "suite": "routing_core",
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(len(results), 1), 3),
        "results": results,
        "completed_at": time.time(),
    }
    if persist:
        summary["eval_run_id"] = _persist_routing_eval_result(summary, workspace)
    return summary


def run_routing_eval_case(case: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Run one Routing Decision eval case in an isolated sandbox workspace."""
    sandbox = _prepare_case_workspace(workspace, str(case.get("id") or "case"))
    _install_case_skills(case, sandbox)
    prompt = str(case.get("prompt") or "")
    intent = classify_user_intent(prompt)
    decision = build_routing_decision(
        prompt,
        workspace_dir=str(sandbox),
        intent_decision=intent,
        team=_default_team(),
    )
    checks = _score_routing_decision(case, decision)
    overall = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "id": case.get("id"),
        "prompt": prompt,
        "overall": overall,
        "intent": intent,
        "decision": decision,
        "checks": checks,
    }


def _prepare_case_workspace(workspace: Path, case_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in case_id).strip("-") or "case"
    sandbox = _evals_root(workspace) / "routing_sandbox" / safe
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def _install_case_skills(case: dict[str, Any], sandbox: Path) -> None:
    for skill_id in case.get("setup_skills", []) if isinstance(case.get("setup_skills"), list) else []:
        if skill_id == "python-dev":
            import_skill(
                "Python Dev",
                "# Python Dev\n\nUse focused Python edits and pytest validation.",
                str(sandbox),
                skill_json={
                    "id": "python-dev",
                    "triggers": ["python", "pytest", "重构"],
                    "agent_roles": ["coder", "tester", "reviewer"],
                    "tool_permissions": ["read_only", "safe_write", "shell_safe"],
                    "quality_rules": ["Keep Python changes focused and validate with targeted tests when requested."],
                },
            )


def _default_team() -> list[dict[str, Any]]:
    return [
        {"role": "lead", "name": "Lead"},
        {"role": "planner", "name": "Planner"},
        {"role": "coder", "name": "Coder"},
        {"role": "tester", "name": "Tester"},
        {"role": "reviewer", "name": "Reviewer"},
        {"role": "security", "name": "Security"},
    ]


def _score_routing_decision(case: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _expect(checks, "route", decision.get("route") == case.get("expected_route"), decision.get("route"), case.get("expected_route"))
    if case.get("expected_execution_route"):
        _expect(
            checks,
            "execution_route",
            decision.get("execution_route") == case.get("expected_execution_route"),
            decision.get("execution_route"),
            case.get("expected_execution_route"),
        )
    if case.get("expected_next_action"):
        _expect(
            checks,
            "next_action",
            decision.get("next_action") == case.get("expected_next_action"),
            decision.get("next_action"),
            case.get("expected_next_action"),
        )
    if case.get("expected_risk"):
        _expect(checks, "risk", decision.get("risk") == case.get("expected_risk"), decision.get("risk"), case.get("expected_risk"))

    requires = decision.get("requires") if isinstance(decision.get("requires"), dict) else {}
    for key, field in [
        ("must_require_read", "workspace_read"),
        ("must_require_write", "workspace_write"),
        ("must_require_shell", "shell"),
        ("must_require_approval", "approval"),
        ("must_require_execution", "execution"),
    ]:
        if key in case:
            _expect(checks, field, bool(requires.get(field)) is bool(case.get(key)), requires.get(field), case.get(key))

    agents = {str(agent.get("role") or "").lower() for agent in decision.get("agents", []) if isinstance(agent, dict)}
    for role in case.get("required_agents", []) if isinstance(case.get("required_agents"), list) else []:
        _expect(checks, f"required_agent:{role}", role.lower() in agents, sorted(agents), role)
    for role in case.get("forbidden_agents", []) if isinstance(case.get("forbidden_agents"), list) else []:
        _expect(checks, f"forbidden_agent:{role}", role.lower() not in agents, sorted(agents), f"no {role}")
    if "expected_agent_count" in case:
        _expect(
            checks,
            "agent_count",
            len(agents) == int(case.get("expected_agent_count")),
            len(agents),
            case.get("expected_agent_count"),
        )

    skill_ids = {str(skill.get("id") or "") for skill in decision.get("skills", []) if isinstance(skill, dict)}
    if case.get("forbid_skills"):
        _expect(checks, "no_skills", not skill_ids, sorted(skill_ids), [])
    for skill_id in case.get("expected_skills", []) if isinstance(case.get("expected_skills"), list) else []:
        _expect(checks, f"expected_skill:{skill_id}", skill_id in skill_ids, sorted(skill_ids), skill_id)

    mcp_servers = {str(item.get("server_id") or "") for item in decision.get("mcp_plan", []) if isinstance(item, dict)}
    if case.get("forbid_mcp"):
        _expect(checks, "no_mcp", not mcp_servers, sorted(mcp_servers), [])
    for server_id in case.get("expected_mcp_servers", []) if isinstance(case.get("expected_mcp_servers"), list) else []:
        _expect(checks, f"expected_mcp:{server_id}", server_id in mcp_servers, sorted(mcp_servers), server_id)

    _expect(checks, "summary_shape", isinstance(decision.get("summary"), dict), type(decision.get("summary")).__name__, "dict")
    _expect(checks, "requires_shape", isinstance(decision.get("requires"), dict), type(decision.get("requires")).__name__, "dict")
    return checks


def _expect(checks: list[dict[str, Any]], check_id: str, ok: bool, actual: Any, expected: Any) -> None:
    checks.append({
        "id": check_id,
        "status": "passed" if ok else "failed",
        "actual": actual,
        "expected": expected,
    })


def _persist_routing_eval_result(summary: dict[str, Any], workspace: Path) -> str:
    eval_run_id = f"routing-core-{int(time.time() * 1000)}"
    result_dir = _evals_root(workspace) / "routing" / eval_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return eval_run_id


def get_routing_eval_run(eval_run_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read a persisted Routing Decision eval run."""
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in eval_run_id).strip("-")
    result_path = _evals_root(_workspace(workspace_dir)) / "routing" / safe_id / "result.json"
    if not result_path.exists():
        raise ValueError(f"Routing eval run 不存在: {eval_run_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))
