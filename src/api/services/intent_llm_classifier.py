"""Structured LLM intent adapter for Intent Router V3.

This module keeps the LLM-facing classification separate from deterministic
guards and backend normalization. It currently adapts the existing lightweight
strategy classifier into the richer V3 shape; the prompt can later be upgraded
without changing the rest of the router pipeline.
"""

from __future__ import annotations

from typing import Any

from src.api.models import IntentDecision


async def classify_intent_v3_with_llm(
    prompt: str,
    *,
    conversation_summary: str = "",
    fallback: IntentDecision,
) -> dict[str, Any] | None:
    """Return a raw V3-like intent payload from the LLM classifier."""
    from src.agent.strategy.classifier import classify_with_llm

    result = await classify_with_llm(prompt, conversation_summary)
    if not result:
        return None

    strategy = str(result.get("strategy") or fallback.strategy)
    complexity = str(result.get("complexity") or fallback.complexity or fallback.level)
    route = _route_from_strategy(strategy, fallback)
    roles = _normalize_roles(result.get("needed_roles") or fallback.suggested_agents)
    permissions = _permissions_for_route(route, complexity)

    requires_write = route in {"small_edit", "feature_delivery", "debug_fix", "risky_operation"}
    requires_shell = route in {"debug_fix", "test_only", "risky_operation"} or bool(fallback.requires_shell)
    risk_level = "high" if route == "risky_operation" or complexity == "high_risk" else "low"
    return {
        "route": route,
        "complexity": complexity,
        "level": complexity,
        "strategy": strategy,
        "confidence": float(result.get("confidence") or 0),
        "requires_workspace_read": route != "direct_answer",
        "requires_workspace_write": requires_write,
        "requires_shell": requires_shell,
        "requires_approval": route == "risky_operation",
        "risk_level": risk_level,
        "risk_reasons": ["LLM classified high-risk complexity."] if risk_level == "high" else [],
        "suggested_agents": roles,
        "suggested_agent_specs": [
            {
                "role": role,
                "mode": "permanent" if role.lower() == "lead" else "temporary",
                "goal": _agent_goal(role, route),
                "permissions": permissions.get(role.lower(), ["read_only"]),
                "exit_condition": "完成本轮职责或输出需要 Lead 合并的证据。",
            }
            for role in roles
        ],
        "acceptance_criteria": _acceptance_criteria(route),
        "context_requirements": _context_requirements(route),
        "tool_permissions": _tool_permissions(route),
        "rationale": str(result.get("rationale") or fallback.rationale),
        "raw_llm_result": result,
    }


def _route_from_strategy(strategy: str, fallback: IntentDecision) -> str:
    mapping = {
        "analysis_only": "read_only" if fallback.requires_workspace_read else "direct_answer",
        "docs_only": "small_edit",
        "small_patch": "small_edit",
        "bug_fix": "debug_fix",
        "refactor": "feature_delivery",
        "feature_delivery": "feature_delivery",
    }
    return mapping.get(strategy, fallback.route)


def _normalize_roles(raw_roles: Any) -> list[str]:
    roles = [str(role).strip().title() for role in raw_roles or [] if str(role).strip()]
    if "Lead" not in roles:
        roles.insert(0, "Lead")
    result: list[str] = []
    for role in roles:
        if role.lower() not in {item.lower() for item in result}:
            result.append(role)
    return result or ["Lead"]


def _permissions_for_route(route: str, complexity: str) -> dict[str, list[str]]:
    base = {"lead": ["read_only"]}
    if route in {"small_edit", "feature_delivery", "debug_fix"}:
        base["coder"] = ["read_only", "safe_write"]
    if route in {"feature_delivery", "debug_fix", "test_only"}:
        base["tester"] = ["read_only", "shell_safe"]
    if route in {"review_only", "feature_delivery", "debug_fix"}:
        base["reviewer"] = ["read_only"]
    if route == "risky_operation" or complexity == "high_risk":
        base["security"] = ["read_only"]
    return base


def _tool_permissions(route: str) -> dict[str, str]:
    permissions = {"read_file": "read_only", "search_codebase": "read_only", "project_context": "read_only"}
    if route in {"small_edit", "feature_delivery", "debug_fix"}:
        permissions["write_file"] = "safe_write"
    if route in {"feature_delivery", "debug_fix", "test_only"}:
        permissions["run_command"] = "shell_safe"
    if route == "risky_operation":
        permissions.update({"delete_file": "risky_write", "run_command": "shell_risky"})
    return permissions


def _context_requirements(route: str) -> dict[str, bool]:
    return {
        "need_project_index": route not in {"direct_answer", "clarification_needed"},
        "need_recent_changes": route in {"debug_fix", "review_only", "feature_delivery"},
        "need_failure_context": route == "debug_fix",
        "need_conversation_memory": True,
    }


def _acceptance_criteria(route: str) -> list[str]:
    if route == "direct_answer":
        return ["Lead 直接回答用户问题，不创建任务卡。"]
    if route == "read_only":
        return ["读取相关项目证据并给出结论。"]
    if route == "small_edit":
        return ["完成局部文件修改。", "说明修改内容。"]
    if route == "debug_fix":
        return ["定位失败原因。", "完成局部修复。", "给出验证证据。"]
    if route == "test_only":
        return ["运行或说明验证命令。", "汇总测试结果。"]
    if route == "risky_operation":
        return ["进入审批流程。", "未经批准不执行高风险动作。"]
    return ["完成用户请求的代码任务。", "给出变更和验证证据。"]


def _agent_goal(role: str, route: str) -> str:
    role_key = role.lower()
    if role_key == "lead":
        return "判断任务边界，分配上下文，收敛最终结果。"
    if role_key == "coder":
        return "实现必要代码或文件修改。"
    if role_key == "tester":
        return "验证实现是否满足验收条件。"
    if role_key == "reviewer":
        return "检查风险、回归和交付质量。"
    if role_key == "security":
        return "审查高风险操作和权限边界。"
    return f"围绕 {route} 完成本轮专项分析。"
