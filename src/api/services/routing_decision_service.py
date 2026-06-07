"""Build the runtime-facing Routing Decision.

This service keeps the existing Intent Router as the source of truth, then
binds capability selection, Skill evidence, MCP planning, and the next runtime
action into one auditable object.
"""

from __future__ import annotations

from typing import Any

from src.api.services.capability_service import recommend_capabilities
from src.api.services.intent_router import classify_user_intent
from src.api.services.skill_registry_service import preview_skill_selection


def build_routing_decision(
    prompt: str,
    *,
    workspace_dir: str | None = None,
    intent_decision: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    team: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a schema-like Routing Decision for one run.

    The decision is intentionally deterministic and serializable. LLM-assisted
    classification may already have happened upstream; this layer only repairs,
    enriches, and audits the result.
    """
    intent = dict(intent_decision or classify_user_intent(prompt))
    plan = execution_plan if isinstance(execution_plan, dict) else {}
    route = str(intent.get("route") or "direct_answer")
    execution_route = str(intent.get("execution_route") or "lead_direct_reply")
    direct = execution_route == "lead_direct_reply"

    capability_source = _capability_source(prompt, workspace_dir, plan, direct)
    skill_preview = _skill_preview(prompt, workspace_dir, team or [], direct)
    agents = _agent_specs(intent, team or [], direct)
    next_action = _next_action(intent, skill_preview, capability_source)
    repair_notes = _repair_notes(intent, skill_preview, capability_source, direct)

    return {
        "schema_version": "routing-decision-1",
        "intent": str(intent.get("intent") or route),
        "route": route,
        "execution_route": execution_route,
        "complexity": str(intent.get("complexity") or intent.get("level") or "simple"),
        "risk": str(intent.get("risk_level") or "low"),
        "confidence": float(intent.get("confidence") or 0.0),
        "next_action": next_action,
        "reason": str(intent.get("rationale") or ""),
        "requires": {
            "workspace_read": bool(intent.get("requires_workspace_read")),
            "workspace_write": bool(intent.get("requires_workspace_write")),
            "shell": bool(intent.get("requires_shell")),
            "approval": bool(intent.get("requires_approval")),
            "execution": bool(intent.get("requires_execution")),
        },
        "skills": skill_preview.get("selected", []),
        "omitted_skills": skill_preview.get("omitted", []),
        "mcp_plan": capability_source.get("mcp_plan", []),
        "agents": agents,
        "tool_policy": _compact_tool_policy(plan.get("tool_policy")),
        "signals": intent.get("signals", []) if isinstance(intent.get("signals"), list) else [],
        "guard_hits": intent.get("guard_hits", []) if isinstance(intent.get("guard_hits"), list) else [],
        "repair_notes": repair_notes,
        "summary": {
            "skill_count": len(skill_preview.get("selected", [])),
            "omitted_skill_count": len(skill_preview.get("omitted", [])),
            "mcp_count": len(capability_source.get("mcp_plan", [])),
            "usable_mcp_count": sum(1 for item in capability_source.get("mcp_plan", []) if item.get("usable")),
            "agent_count": len(agents),
            "capability_source": capability_source.get("source", "none"),
        },
    }


def _capability_source(
    prompt: str,
    workspace_dir: str | None,
    execution_plan: dict[str, Any],
    direct: bool,
) -> dict[str, Any]:
    if direct:
        return {"source": "lead_direct_reply", "mcp_plan": [], "capabilities": []}
    plan_mcp = execution_plan.get("mcp_plan")
    plan_capabilities = execution_plan.get("capabilities")
    if isinstance(plan_mcp, list) and plan_mcp:
        return {
            "source": "execution_plan",
            "mcp_plan": [item for item in plan_mcp if isinstance(item, dict)],
            "capabilities": [str(item) for item in plan_capabilities or [] if item],
        }
    recommended = recommend_capabilities(prompt, workspace_dir)
    return {
        "source": "capability_recommendation",
        "mcp_plan": [
            item for item in recommended.get("mcp_plan", [])
            if isinstance(item, dict)
        ],
        "capabilities": [
            str(item.get("id"))
            for item in recommended.get("capabilities", [])
            if isinstance(item, dict) and item.get("id")
        ],
    }


def _skill_preview(
    prompt: str,
    workspace_dir: str | None,
    team: list[dict[str, Any]],
    direct: bool,
) -> dict[str, Any]:
    if direct:
        return {
            "selected": [],
            "omitted": [],
            "summary": {
                "selected": 0,
                "omitted": 0,
                "context_budget": 0,
                "skipped": "lead_direct_reply",
            },
        }
    return preview_skill_selection(prompt, workspace_dir, team=team, max_skills=5)


def _agent_specs(intent: dict[str, Any], team: list[dict[str, Any]], direct: bool) -> list[dict[str, Any]]:
    if direct:
        return [{
            "role": "Lead",
            "name": "Lead",
            "temporary": False,
            "tool_permissions": ["read_only"],
            "reason": "lead_direct_reply",
        }]
    specs = intent.get("suggested_agent_specs")
    agents: list[dict[str, Any]] = []
    if isinstance(specs, list):
        for item in specs:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip() or "Agent"
            agents.append({
                "role": role,
                "name": _name_for_role(role, team),
                "temporary": item.get("mode", "temporary") != "permanent",
                "tool_permissions": item.get("permissions", []) if isinstance(item.get("permissions"), list) else [],
                "reason": item.get("goal") or "intent_suggested_agent",
            })
    if agents:
        return agents
    for role in intent.get("suggested_agents", []) if isinstance(intent.get("suggested_agents"), list) else ["Lead"]:
        role_text = str(role or "Agent")
        agents.append({
            "role": role_text,
            "name": _name_for_role(role_text, team),
            "temporary": role_text.lower() != "lead",
            "tool_permissions": [],
            "reason": "intent_suggested_agent",
        })
    return agents or [{"role": "Lead", "name": "Lead", "temporary": False, "tool_permissions": [], "reason": "fallback"}]


def _name_for_role(role: str, team: list[dict[str, Any]]) -> str:
    role_lower = role.lower()
    for member in team:
        if str(member.get("role", "")).lower() == role_lower:
            return str(member.get("name") or role)
    return "Lead" if role_lower == "lead" else f"{role} Agent"


def _next_action(
    intent: dict[str, Any],
    skill_preview: dict[str, Any],
    capability_source: dict[str, Any],
) -> str:
    route = str(intent.get("route") or "")
    if route == "clarification_needed":
        return "ask_clarification"
    if str(intent.get("execution_route") or "") == "lead_direct_reply":
        return "answer_directly"
    if bool(intent.get("requires_approval")):
        return "request_approval"
    if route == "test_only":
        return "run_checks"
    if capability_source.get("mcp_plan") and not bool(intent.get("requires_workspace_write")):
        return "select_mcp_tools"
    if route in {"read_only", "review_only"}:
        return "inspect_files"
    if route == "small_edit":
        return "edit_with_lead"
    if skill_preview.get("selected") or route in {"feature_delivery", "debug_fix", "risky_operation"}:
        return "create_agents"
    return "inspect_files"


def _repair_notes(
    intent: dict[str, Any],
    skill_preview: dict[str, Any],
    capability_source: dict[str, Any],
    direct: bool,
) -> list[str]:
    notes: list[str] = []
    if direct and skill_preview.get("selected"):
        notes.append("direct route dropped selected skills")
    if direct and capability_source.get("mcp_plan"):
        notes.append("direct route dropped mcp plan")
    if bool(intent.get("requires_approval")) and str(intent.get("risk_level")) != "high":
        notes.append("approval required with non-high risk")
    return notes


def _compact_tool_policy(tool_policy: Any) -> dict[str, Any]:
    if not isinstance(tool_policy, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("mode", "risk_level", "strategy"):
        if tool_policy.get(key):
            result[key] = str(tool_policy.get(key))[:100]
    for key in ("allowed_tools", "denied_tools", "approval_required", "approval_required_levels", "recommended_tools"):
        value = tool_policy.get(key)
        if isinstance(value, list):
            result[key] = [str(item)[:100] for item in value[:32] if item]
    return result
