"""Ephemeral sub-agent lifecycle service.

Temporary agents are scoped to one run. They are suggested by the lead agent,
optionally spawned for a bounded task, then archived after completion/failure so
the persistent team stays clean.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.api.services.event_store import get_event_store
from src.infra.path_guard import safe_slug


MAX_SUGGESTED_AGENTS = 5
MAX_ACTIVE_AGENTS = 3
DEFAULT_TTL_SECONDS = 30 * 60

ACTIVE_STATUSES = {"suggested", "active", "working", "waiting_input"}
ARCHIVED_STATUSES = {"archived", "expired"}


def _now() -> float:
    return time.time()


def _run_dir(thread_id: str, workspace_dir: str) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir)


def _state_path(thread_id: str, workspace_dir: str) -> Path:
    return _run_dir(thread_id, workspace_dir) / "ephemeral_agents.json"


def _event_path(thread_id: str, workspace_dir: str) -> Path:
    return _run_dir(thread_id, workspace_dir) / "ephemeral_agent_events.jsonl"


def _default_state(thread_id: str) -> dict[str, Any]:
    return {"thread_id": thread_id, "agents": []}


def _read_state(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    path = _state_path(thread_id, workspace_dir)
    if not path.exists():
        return _default_state(thread_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state(thread_id)
    if not isinstance(data, dict):
        return _default_state(thread_id)
    agents = data.get("agents")
    if not isinstance(agents, list):
        data["agents"] = []
    data["thread_id"] = thread_id
    return data


def _write_state(thread_id: str, workspace_dir: str, state: dict[str, Any]) -> None:
    path = _state_path(thread_id, workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_local_event(thread_id: str, workspace_dir: str, event_type: str, payload: dict[str, Any]) -> None:
    event = {
        "id": f"eagent_event_{uuid.uuid4().hex[:12]}",
        "thread_id": thread_id,
        "type": event_type,
        "timestamp": _now(),
        "payload": payload,
    }
    with _event_path(thread_id, workspace_dir).open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def _emit_agent_event(
    thread_id: str,
    workspace_dir: str,
    event_type: str,
    agent: dict[str, Any],
    content: str = "",
) -> None:
    payload = {
        "agent_id": agent.get("agent_id", ""),
        "name": agent.get("name", ""),
        "role": agent.get("role", ""),
        "status": agent.get("status", ""),
        "goal": agent.get("goal", ""),
        "reason": agent.get("reason", ""),
        "tools": agent.get("tools", []),
        "capabilities": agent.get("capabilities", []),
        "mcp_servers": agent.get("mcp_servers", []),
    }
    get_event_store().append_event(
        thread_id,
        event_type,
        title=f"{agent.get('name', 'Ephemeral Agent')}: {agent.get('status', '')}",
        content=content or agent.get("reason", ""),
        agent=agent.get("name") or "Lead",
        payload=payload,
        workspace_dir=workspace_dir,
    )
    _append_local_event(thread_id, workspace_dir, event_type, payload)


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _clamp_max_agents(max_agents: int | None) -> int:
    value = int(max_agents or 4)
    return max(1, min(value, MAX_SUGGESTED_AGENTS))


def _scope(include: list[str], exclude: list[str] | None = None, actions: list[str] | None = None) -> dict[str, Any]:
    return {
        "include": include,
        "exclude": exclude or [],
        "allowed_actions": actions or ["read_file", "write_file", "run_command"],
    }


def _expected(summary: bool = True, tests: bool = False, artifact: bool = False) -> dict[str, bool]:
    return {
        "summary_required": summary,
        "tests_required": tests,
        "artifact_required": artifact,
    }


def _suggestion(
    name: str,
    role: str,
    goal: str,
    reason: str,
    capabilities: list[str],
    *,
    risk_level: str = "medium",
    task_scope: dict[str, Any] | None = None,
    mcp_servers: list[str] | None = None,
    blocked_capabilities: list[str] | None = None,
    expected_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "status": "suggested",
        "goal": goal,
        "reason": reason,
        "capabilities": _unique(capabilities),
        "mcp_servers": _unique(mcp_servers or []),
        "blocked_capabilities": _unique(blocked_capabilities or []),
        "risk_level": risk_level,
        "task_scope": task_scope or _scope(["."], []),
        "expected_output": expected_output or _expected(),
    }


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _mcp_plan_by_id(mcp_plan: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("server_id")): item for item in mcp_plan if isinstance(item, dict) and item.get("server_id")}


def suggest_ephemeral_agents(
    prompt: str,
    mcp_plan: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
    max_agents: int | None = 4,
) -> dict[str, Any]:
    """Suggest short-lived sub-agents for one task."""
    del workspace_dir  # Reserved for future workspace-structure weighting.
    plan = list(mcp_plan or [])
    mcp_by_id = _mcp_plan_by_id(plan)
    text = str(prompt or "").lower()

    suggestions: list[dict[str, Any]] = []

    if _contains_any(text, ["前端", "界面", "ui", "样式", "交互", "页面", "组件", "frontend"]):
        suggestions.append(_suggestion(
            "Frontend Action Agent",
            "frontend_worker",
            "实现或修复前端界面、交互和状态展示。",
            "检测到前端、界面或交互需求，需要独立前端执行者。",
            ["tool.file_ops", "tool.project_index", "skill.frontend-polish"],
            task_scope=_scope(["frontend", "tests", "docs"], ["src/api"]),
            expected_output=_expected(tests=True),
        ))

    if _contains_any(text, ["接口", "api", "服务", "数据库", "状态", "路由", "fastapi", "后端", "backend"]):
        suggestions.append(_suggestion(
            "Backend Action Agent",
            "backend_worker",
            "实现后端服务、路由、数据模型和相关测试。",
            "检测到后端接口、状态或服务层需求，需要独立后端执行者。",
            ["tool.file_ops", "tool.project_index", "skill.delivery-review"],
            task_scope=_scope(["src/api", "src/runtime", "tests", "scripts"], ["frontend"]),
            expected_output=_expected(tests=True),
        ))

    if _contains_any(text, ["测试", "验证", "回归", "覆盖", "pytest", "smoke", "质量"]):
        suggestions.append(_suggestion(
            "Test Action Agent",
            "test_worker",
            "补齐测试、运行验证命令并整理质量证据。",
            "检测到测试或质量验证需求，需要单独测试执行者。",
            ["skill.delivery-review", "tool.project_index", "tool.recovery"],
            task_scope=_scope(["tests", "scripts", "src", "frontend"], []),
            expected_output=_expected(tests=True),
        ))

    if _contains_any(text, ["readme", "文档", "说明", "计划", "接口文档", "docs"]):
        suggestions.append(_suggestion(
            "Docs Action Agent",
            "docs_worker",
            "更新文档、接口说明、开发计划或交付说明。",
            "检测到文档或说明需求，需要独立文档执行者。",
            ["tool.file_ops", "tool.project_index", "mcp.docs"],
            task_scope=_scope(["docs", "README.md"], ["src", "frontend"], ["read_file", "write_file"]),
            expected_output=_expected(artifact=True),
        ))

    if _contains_any(text, ["github", "issue", "pr", "pull request", "ci", "代码审查"]):
        github = mcp_by_id.get("mcp.github")
        suggestions.append(_suggestion(
            "GitHub Context Agent",
            "github_context_agent",
            "读取 GitHub Issue、PR 或 CI 上下文并提供结构化证据。",
            "检测到 GitHub/Issue/PR/CI 需求。",
            ["mcp.github", "skill.delivery-review"],
            risk_level="high",
            task_scope=_scope(["."], [], ["mcp_call"]),
            mcp_servers=["mcp.github"] if github and github.get("usable") else [],
            blocked_capabilities=[] if github and github.get("usable") else ["mcp.github"],
            expected_output=_expected(artifact=True),
        ))

    if _contains_any(text, ["figma", "设计稿", "视觉稿", "design", "handoff"]):
        figma = mcp_by_id.get("mcp.figma")
        suggestions.append(_suggestion(
            "Design Context Agent",
            "design_context_agent",
            "读取设计稿上下文，并输出 UI/组件约束。",
            "检测到设计稿或 Figma 上下文需求。",
            ["mcp.figma", "skill.frontend-polish"],
            risk_level="high",
            task_scope=_scope(["frontend", "docs"], [], ["mcp_call", "read_file"]),
            mcp_servers=["mcp.figma"] if figma and figma.get("usable") else [],
            blocked_capabilities=[] if figma and figma.get("usable") else ["mcp.figma"],
            expected_output=_expected(artifact=True),
        ))

    if _contains_any(text, ["重构", "完整", "系统", "产品级", "端到端", "复杂"]):
        suggestions.append(_suggestion(
            "Reviewer",
            "reviewer",
            "复核跨模块变更、风险、测试证据和最终交付可信度。",
            "检测到复杂或产品级任务，需要临时 Reviewer 收口风险。",
            ["skill.delivery-review", "tool.project_index", "tool.recovery"],
            task_scope=_scope(["."], []),
            expected_output=_expected(tests=True),
        ))

    if not suggestions:
        suggestions.append(_suggestion(
            "Action Agent",
            "implementation_worker",
            "完成本轮主要实现并提交结构化结果。",
            "未检测到专门领域，按通用实现任务创建一个临时执行者。",
            ["tool.file_ops", "tool.project_index", "skill.delivery-review"],
            task_scope=_scope(["."], []),
            expected_output=_expected(tests=True),
        ))

    max_count = _clamp_max_agents(max_agents)
    deduped: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for item in suggestions:
        role = item["role"]
        if role in seen_roles:
            continue
        seen_roles.add(role)
        deduped.append(item)
        if len(deduped) >= max_count:
            break

    return {
        "suggestions": deduped,
        "limits": {
            "max_agents": max_count,
            "max_active_agents": MAX_ACTIVE_AGENTS,
            "default_ttl_seconds": DEFAULT_TTL_SECONDS,
        },
        "mcp_plan_count": len(plan),
    }


def _active_count(agents: list[dict[str, Any]]) -> int:
    return sum(1 for agent in agents if agent.get("status") in ACTIVE_STATUSES)


def _make_agent_id(role: str, existing: set[str]) -> str:
    base = f"eagent_{safe_slug(role or 'worker', max_length=48)}"
    candidate = base
    index = 1
    while candidate in existing:
        index += 1
        candidate = f"{base}_{index:02d}"
    return candidate


def _normalise_agent_spec(
    thread_id: str,
    spec: dict[str, Any],
    existing_ids: set[str],
    status: str = "active",
) -> dict[str, Any]:
    now = _now()
    role = str(spec.get("role") or "worker").strip() or "worker"
    tools = _unique([str(item) for item in spec.get("tools", []) if item])
    task_scope = spec.get("task_scope") if isinstance(spec.get("task_scope"), dict) else None
    if task_scope is None:
        task_scope = _scope(["."], [], tools or None)
    elif tools and not task_scope.get("allowed_actions"):
        task_scope = {**task_scope, "allowed_actions": tools}
    agent_id = str(spec.get("agent_id") or spec.get("id") or "").strip()
    if not agent_id:
        agent_id = _make_agent_id(role, existing_ids)
    elif agent_id in existing_ids:
        agent_id = _make_agent_id(agent_id, existing_ids)

    return {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "parent_agent": str(spec.get("parent_agent") or "Lead"),
        "name": str(spec.get("name") or role.replace("_", " ").title()),
        "role": role,
        "status": status,
        "goal": str(spec.get("goal") or ""),
        "reason": str(spec.get("reason") or ""),
        "tools": tools,
        "capabilities": _unique([str(item) for item in spec.get("capabilities", []) if item]),
        "mcp_servers": _unique([str(item) for item in spec.get("mcp_servers", []) if item]),
        "blocked_capabilities": _unique([str(item) for item in spec.get("blocked_capabilities", []) if item]),
        "risk_level": str(spec.get("risk_level") or "medium"),
        "task_scope": task_scope,
        "expected_output": spec.get("expected_output") if isinstance(spec.get("expected_output"), dict) else _expected(),
        "created_at": now,
        "started_at": now if status in {"active", "working"} else 0,
        "completed_at": 0,
        "archived_at": 0,
        "expires_at": now + int(spec.get("ttl_seconds") or DEFAULT_TTL_SECONDS),
        "result": {},
    }


def spawn_ephemeral_agent(thread_id: str, spec: dict[str, Any], workspace_dir: str) -> dict[str, Any]:
    """Activate one ephemeral agent for a run."""
    state = _read_state(thread_id, workspace_dir)
    agents = state["agents"]
    if _active_count(agents) >= MAX_ACTIVE_AGENTS:
        raise ValueError(f"临时子 Agent 数量已达到上限: {MAX_ACTIVE_AGENTS}")

    existing_ids = {str(agent.get("agent_id")) for agent in agents}
    agent = _normalise_agent_spec(thread_id, spec, existing_ids, status="active")
    agents.append(agent)
    _write_state(thread_id, workspace_dir, state)
    _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_spawned", agent, agent.get("reason", ""))
    return agent


def list_ephemeral_agents(
    thread_id: str,
    workspace_dir: str,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return active ephemeral agents, optionally including archived history."""
    cleanup_expired_ephemeral_agents(thread_id, workspace_dir)
    state = _read_state(thread_id, workspace_dir)
    agents = state["agents"]
    visible = agents if include_archived else [agent for agent in agents if agent.get("status") not in ARCHIVED_STATUSES]
    return {
        "thread_id": thread_id,
        "agents": visible,
        "total": len(visible),
        "active_count": _active_count(agents),
        "archived_count": sum(1 for agent in agents if agent.get("status") in ARCHIVED_STATUSES),
        "limits": {
            "max_active_agents": MAX_ACTIVE_AGENTS,
            "max_suggested_agents": MAX_SUGGESTED_AGENTS,
        },
    }


def summarize_ephemeral_agent_contributions(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Return a stable delivery/report summary for task-scoped agents."""
    state = _read_state(thread_id, workspace_dir)
    agents = state["agents"]
    contributions: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    next_actions: list[str] = []

    for agent in agents:
        result = agent.get("result") if isinstance(agent.get("result"), dict) else {}
        evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
        agent_risks = result.get("risks") if isinstance(result.get("risks"), list) else []
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        recommended = (
            result.get("recommended_next_actions")
            if isinstance(result.get("recommended_next_actions"), list)
            else []
        )
        contribution = {
            "agent_id": str(agent.get("agent_id") or ""),
            "name": str(agent.get("name") or ""),
            "role": str(agent.get("role") or ""),
            "status": str(agent.get("status") or ""),
            "terminal_status": str(agent.get("terminal_status") or ""),
            "summary": str(result.get("summary") or agent.get("reason") or agent.get("goal") or ""),
            "evidence_count": len(evidence),
            "risk_count": len(agent_risks),
            "artifact_count": len(artifacts),
            "artifacts": artifacts[:12],
            "recommended_next_actions": [str(item) for item in recommended if item],
        }
        if contribution["summary"] or contribution["terminal_status"] or contribution["status"] in ARCHIVED_STATUSES:
            contributions.append(contribution)
        if agent.get("status") not in ARCHIVED_STATUSES:
            pending.append({
                "agent_id": contribution["agent_id"],
                "name": contribution["name"],
                "role": contribution["role"],
                "status": contribution["status"],
            })
        for risk in agent_risks:
            if isinstance(risk, dict):
                risks.append({
                    "source": "ephemeral_agent",
                    "agent_id": contribution["agent_id"],
                    "agent_name": contribution["name"],
                    **risk,
                })
            else:
                risks.append({
                    "source": "ephemeral_agent",
                    "agent_id": contribution["agent_id"],
                    "agent_name": contribution["name"],
                    "description": str(risk),
                })
        next_actions.extend(contribution["recommended_next_actions"])

    return {
        "thread_id": thread_id,
        "contributions": contributions,
        "pending_agents": pending,
        "risks": risks,
        "next_actions": _unique(next_actions),
        "summary": {
            "total": len(agents),
            "active_count": sum(1 for agent in agents if agent.get("status") not in ARCHIVED_STATUSES),
            "completed_count": sum(1 for agent in agents if agent.get("terminal_status") == "completed"),
            "archived_count": sum(1 for agent in agents if agent.get("status") in ARCHIVED_STATUSES),
        },
    }


def _find_agent(agents: list[dict[str, Any]], agent_id: str) -> dict[str, Any] | None:
    for agent in agents:
        if agent.get("agent_id") == agent_id:
            return agent
    return None


def update_ephemeral_agent_status(
    thread_id: str,
    agent_id: str,
    status: str,
    workspace_dir: str,
    reason: str = "",
) -> dict[str, Any]:
    """Update one temporary agent status and emit a progress event."""
    state = _read_state(thread_id, workspace_dir)
    agent = _find_agent(state["agents"], agent_id)
    if agent is None:
        raise ValueError(f"临时子 Agent 不存在: {agent_id}")
    if agent.get("status") in ARCHIVED_STATUSES:
        raise ValueError(f"临时子 Agent 已归档: {agent_id}")

    now = _now()
    agent["status"] = str(status or agent.get("status") or "active")
    agent["last_active_at"] = now
    if agent["status"] in {"active", "working"} and not agent.get("started_at"):
        agent["started_at"] = now
    if reason:
        agent["last_action"] = reason
    _write_state(thread_id, workspace_dir, state)
    _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_updated", agent, reason)
    return agent


def complete_ephemeral_agent(
    thread_id: str,
    agent_id: str,
    result: dict[str, Any],
    workspace_dir: str,
) -> dict[str, Any]:
    """Mark a temporary agent as completed, then archive it from the active list."""
    state = _read_state(thread_id, workspace_dir)
    agent = _find_agent(state["agents"], agent_id)
    if agent is None:
        raise ValueError(f"临时子 Agent 不存在: {agent_id}")
    if agent.get("status") in ARCHIVED_STATUSES:
        raise ValueError(f"临时子 Agent 已归档: {agent_id}")

    now = _now()
    agent["terminal_status"] = "completed"
    agent["status"] = "archived"
    agent["completed_at"] = now
    agent["archived_at"] = now
    agent["result"] = {
        "summary": str(result.get("summary") or ""),
        "evidence": result.get("evidence") if isinstance(result.get("evidence"), list) else [],
        "risks": result.get("risks") if isinstance(result.get("risks"), list) else [],
        "artifacts": result.get("artifacts") if isinstance(result.get("artifacts"), list) else [],
        "recommended_next_actions": (
            result.get("recommended_next_actions")
            if isinstance(result.get("recommended_next_actions"), list)
            else []
        ),
    }
    _write_state(thread_id, workspace_dir, state)
    _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_completed", agent, agent["result"]["summary"])
    _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_archived", agent, "临时子 Agent 完成后自动归档。")
    return agent


def archive_ephemeral_agent(
    thread_id: str,
    agent_id: str,
    reason: str,
    workspace_dir: str,
) -> dict[str, Any]:
    """Archive a temporary agent without marking it completed."""
    state = _read_state(thread_id, workspace_dir)
    agent = _find_agent(state["agents"], agent_id)
    if agent is None:
        raise ValueError(f"临时子 Agent 不存在: {agent_id}")
    if agent.get("status") in ARCHIVED_STATUSES:
        return agent

    now = _now()
    agent["terminal_status"] = agent.get("terminal_status") or "cancelled"
    agent["status"] = "archived"
    agent["archived_at"] = now
    agent["archive_reason"] = reason
    _write_state(thread_id, workspace_dir, state)
    _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_archived", agent, reason)
    return agent


def cleanup_expired_ephemeral_agents(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Archive active temporary agents whose TTL has elapsed."""
    state = _read_state(thread_id, workspace_dir)
    changed = False
    expired: list[dict[str, Any]] = []
    now = _now()
    for agent in state["agents"]:
        if agent.get("status") in ARCHIVED_STATUSES:
            continue
        expires_at = float(agent.get("expires_at") or 0)
        if expires_at and expires_at <= now:
            agent["terminal_status"] = "expired"
            agent["status"] = "expired"
            agent["archived_at"] = now
            expired.append(agent)
            changed = True

    if changed:
        _write_state(thread_id, workspace_dir, state)
        for agent in expired:
            _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_expired", agent, "临时子 Agent 已超时归档。")

    return {
        "thread_id": thread_id,
        "expired_count": len(expired),
        "expired_agents": expired,
    }
