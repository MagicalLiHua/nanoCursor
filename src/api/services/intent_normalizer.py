"""Backend policy normalization for Intent Router V3."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from src.api.models import AgentIntentSpec, IntentDecision, IntentRoute
from src.api.services.intent_guards import IntentGuardResult


WRITE_ROUTES = {"small_edit", "feature_delivery", "debug_fix", "risky_operation"}
SHELL_ROUTES = {"feature_delivery", "debug_fix", "test_only", "risky_operation"}
DIRECT_ROUTES = {"direct_answer", "clarification_needed"}
READ_ONLY_ROUTES = {"read_only", "review_only"}


def normalize_intent_decision(
    raw: dict[str, Any] | None,
    *,
    fallback: IntentDecision,
    guards: IntentGuardResult,
) -> IntentDecision:
    """Normalize raw model output into the stable IntentDecision contract."""
    if guards.hard_decision is not None:
        decision = guards.hard_decision
        decision.raw_decision = raw or {}
        decision.guard_hits = guards.hits
        decision.risk_level = guards.risk_level
        decision.risk_reasons = guards.risk_reasons
        decision.normalized_from = "hard_guard"
        decision.source = decision.source or "deterministic_guard"
        _fill_v3_defaults(decision)
        return decision

    raw = raw or {}
    route = _valid_route(raw.get("route"), fallback.route)
    complexity = _valid_complexity(raw.get("complexity") or raw.get("level"), fallback.level)
    confidence = _confidence(raw.get("confidence"), fallback.confidence)
    requires_read = bool(raw.get("requires_workspace_read", fallback.requires_workspace_read))
    requires_write = bool(raw.get("requires_workspace_write", fallback.requires_workspace_write))
    requires_shell = bool(raw.get("requires_shell", fallback.requires_shell))
    requires_approval = bool(raw.get("requires_approval", fallback.requires_approval))

    route, complexity, requires_read, requires_write, requires_shell, requires_approval = _apply_policy_bounds(
        route,
        complexity,
        requires_read,
        requires_write,
        requires_shell,
        requires_approval,
        fallback,
        guards,
    )
    suggested_agents = _normalize_agent_names(raw.get("suggested_agents") or fallback.suggested_agents, route)
    agent_specs = _normalize_agent_specs(raw.get("suggested_agent_specs"), suggested_agents, route)
    decision = IntentDecision(
        route=route,  # type: ignore[arg-type]
        confidence=confidence,
        requires_workspace_read=requires_read,
        requires_workspace_write=requires_write,
        requires_shell=requires_shell,
        requires_approval=requires_approval,
        requires_execution=route not in DIRECT_ROUTES,
        suggested_agents=suggested_agents,
        rationale=str(raw.get("rationale") or fallback.rationale),
        missing_information=[
            str(item) for item in raw.get("missing_information", fallback.missing_information) or []
            if str(item).strip()
        ],
        intent=str(raw.get("intent") or fallback.intent or f"llm_{raw.get('strategy', fallback.strategy)}"),
        level=complexity,
        complexity=complexity,
        strategy=str(raw.get("strategy") or fallback.strategy),
        execution_route="lead_direct_reply" if route in DIRECT_ROUTES else "agenthub_delivery",
        signals=_unique([*fallback.signals, "llm_classified"] if raw else fallback.signals),
        indicators=_unique([*fallback.indicators, *guards.hits]),
        source="normalized_llm_intent" if raw else fallback.source,
        risk_level=_risk_level(raw, guards, route),
        risk_reasons=_risk_reasons(raw, guards, route),
        acceptance_criteria=[
            str(item) for item in raw.get("acceptance_criteria", []) if str(item).strip()
        ],
        context_requirements=raw.get("context_requirements") if isinstance(raw.get("context_requirements"), dict) else {},
        tool_permissions=raw.get("tool_permissions") if isinstance(raw.get("tool_permissions"), dict) else {},
        suggested_agent_specs=[item.model_dump() for item in agent_specs],
        guard_hits=guards.hits,
        normalized_from="llm_structured_intent" if raw else "deterministic_fallback",
        raw_decision=raw,
    )
    _fill_v3_defaults(decision)
    return decision


def _apply_policy_bounds(
    route: str,
    complexity: str,
    requires_read: bool,
    requires_write: bool,
    requires_shell: bool,
    requires_approval: bool,
    fallback: IntentDecision,
    guards: IntentGuardResult,
) -> tuple[str, str, bool, bool, bool, bool]:
    if guards.risk_level == "high" or fallback.requires_approval:
        route = "risky_operation"
        complexity = "high_risk"
        requires_read = True
        requires_write = True
        requires_shell = True
        requires_approval = True

    if route in DIRECT_ROUTES:
        return route, "simple", False, False, False, False

    if route in READ_ONLY_ROUTES:
        return route, "simple" if complexity == "simple" else complexity, True, False, requires_shell, False

    if route in WRITE_ROUTES:
        requires_read = True
        requires_write = True

    if route in SHELL_ROUTES:
        requires_shell = True

    if route == "risky_operation":
        complexity = "high_risk"
        requires_approval = True

    return route, complexity, requires_read, requires_write, requires_shell, requires_approval


def _fill_v3_defaults(decision: IntentDecision) -> None:
    if not decision.context_requirements:
        decision.context_requirements = {
            "need_project_index": decision.route not in DIRECT_ROUTES,
            "need_conversation_memory": True,
            "need_recent_changes": decision.route in {"feature_delivery", "debug_fix", "review_only"},
            "need_failure_context": decision.route == "debug_fix",
        }
    if not decision.tool_permissions:
        permissions = {"read_file": "read_only", "search_codebase": "read_only", "project_context": "read_only"}
        if decision.requires_workspace_write:
            permissions["write_file"] = "safe_write" if not decision.requires_approval else "risky_write"
        if decision.requires_shell:
            permissions["run_command"] = "shell_risky" if decision.requires_approval else "shell_safe"
        decision.tool_permissions = permissions
    if not decision.acceptance_criteria:
        decision.acceptance_criteria = _acceptance_defaults(decision.route)
    if not decision.suggested_agent_specs:
        specs = [
            AgentIntentSpec(
                role=role,
                mode="permanent" if role.lower() == "lead" else "temporary",
                goal=_goal_for_role(role),
                permissions=_permissions_for_agent(role, decision),
                exit_condition="完成职责或输出需要 Lead 合并的证据。",
            ).model_dump()
            for role in decision.suggested_agents
        ]
        decision.suggested_agent_specs = specs


def _valid_route(value: Any, fallback: str) -> str:
    routes = set(IntentRoute.__args__)  # type: ignore[attr-defined]
    text = str(value or "").strip()
    return text if text in routes else fallback


def _valid_complexity(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in {"simple", "small_code", "medium", "high_risk"} else str(fallback or "simple")


def _confidence(value: Any, fallback: float) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return round(max(0.0, min(1.0, float(fallback or 0))), 2)


def _risk_level(raw: dict[str, Any], guards: IntentGuardResult, route: str) -> str:
    level = str(raw.get("risk_level") or guards.risk_level or "low")
    if route == "risky_operation":
        return "high"
    return level if level in {"low", "medium", "high"} else "low"


def _risk_reasons(raw: dict[str, Any], guards: IntentGuardResult, route: str) -> list[str]:
    reasons = [str(item) for item in raw.get("risk_reasons", []) if str(item).strip()]
    reasons.extend(guards.risk_reasons)
    if route == "risky_operation" and not reasons:
        reasons.append("Route requires approval.")
    return _unique(reasons)


def _normalize_agent_names(raw: Any, route: str) -> list[str]:
    roles = [str(item).strip().title() for item in raw or [] if str(item).strip()]
    if route in DIRECT_ROUTES:
        return ["Lead"]
    if "Lead" not in roles:
        roles.insert(0, "Lead")
    if route in WRITE_ROUTES and not any(role.lower() == "coder" for role in roles):
        roles.append("Coder")
    if route in {"feature_delivery", "debug_fix", "test_only"} and not any(role.lower() == "tester" for role in roles):
        roles.append("Tester")
    return _unique(roles)


def _normalize_agent_specs(raw: Any, roles: list[str], route: str) -> list[AgentIntentSpec]:
    specs: list[AgentIntentSpec] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                specs.append(AgentIntentSpec(**item))
            except ValidationError:
                continue
    existing = {spec.role.lower() for spec in specs}
    for role in roles:
        if role.lower() not in existing:
            specs.append(
                AgentIntentSpec(
                    role=role,
                    mode="permanent" if role.lower() == "lead" else "temporary",
                    goal=_goal_for_role(role),
                    permissions=["read_only"],
                    exit_condition="完成职责或输出需要 Lead 合并的证据。",
                )
            )
    if route in DIRECT_ROUTES:
        specs = [spec for spec in specs if spec.role.lower() == "lead"][:1]
    return specs


def _permissions_for_agent(role: str, decision: IntentDecision) -> list[str]:
    role_key = role.lower()
    permissions = ["read_only"]
    if role_key == "coder" and decision.requires_workspace_write:
        permissions.append("safe_write" if not decision.requires_approval else "risky_write")
    if role_key == "tester" and decision.requires_shell:
        permissions.append("shell_safe" if not decision.requires_approval else "shell_risky")
    return permissions


def _goal_for_role(role: str) -> str:
    role_key = role.lower()
    if role_key == "lead":
        return "判断任务边界，调度上下文和工具，收敛最终结果。"
    if role_key == "coder":
        return "完成必要代码或文件修改。"
    if role_key == "tester":
        return "验证实现并输出测试证据。"
    if role_key == "reviewer":
        return "复核风险、回归和交付质量。"
    return "完成本轮专项分析。"


def _acceptance_defaults(route: str) -> list[str]:
    if route == "direct_answer":
        return ["Lead 直接回答用户，不创建任务卡。"]
    if route == "clarification_needed":
        return ["明确缺失信息后再决定是否执行。"]
    if route == "read_only":
        return ["只读取项目证据并给出结论。"]
    if route == "risky_operation":
        return ["高风险动作进入审批，未批准不执行。"]
    return ["完成请求对应的任务。", "输出变更、验证或风险证据。"]


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result
