"""Conversation-scoped orchestration for nanoCursor."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from src.api.services.agent_state import DEFAULT_TEAM
from src.api.services.capability_service import recommend_capabilities
from src.api.services.intent_router import classify_user_intent
from src.infra import config as config_module


AGENT_PRESETS: dict[str, dict[str, Any]] = {
    member["name"].lower(): member for member in DEFAULT_TEAM
}

AGENT_PRESETS.update(
    {
        "planner": {
            "name": "Planner",
            "role": "planner",
            "goal": "Clarify task scope, split work into stages, and define acceptance criteria.",
            "tools": ["project_context", "search_codebase", "task_create", "task_update"],
            "capabilities": ["tool.project_index", "tool.memory"],
            "artifacts": ["plan", "acceptance_criteria"],
        },
        "coder": {
            "name": "Coder",
            "role": "coder",
            "goal": "Implement focused workspace changes and keep diffs reviewable.",
            "tools": ["read_file", "write_file", "edit_file", "list_directory"],
            "capabilities": ["tool.file_ops", "tool.project_index"],
            "artifacts": ["changed_files", "diff_summary"],
        },
        "tester": {
            "name": "Tester",
            "role": "tester",
            "goal": "Run appropriate checks, collect evidence, and identify regressions.",
            "tools": ["run_tests", "bash", "read_file"],
            "capabilities": ["skill.delivery-review", "tool.recovery"],
            "artifacts": ["test_results", "quality_notes"],
        },
        "designer": {
            "name": "Designer",
            "role": "designer",
            "goal": "Translate product intent into clear, usable interface decisions.",
            "tools": ["design_review", "ui_spec"],
            "capabilities": ["skill.frontend-polish", "mcp.figma"],
            "artifacts": ["ui_notes", "interaction_risks"],
        },
        "reviewer": {
            "name": "Reviewer",
            "role": "reviewer",
            "goal": "Review changes, risks, and delivery evidence before handoff.",
            "tools": ["diff", "quality", "report"],
            "capabilities": ["skill.delivery-review", "tool.project_index"],
            "artifacts": ["review_notes", "risks"],
        },
        "devops": {
            "name": "DevOps",
            "role": "devops",
            "goal": "Validate environment, build, deployment, and rollback concerns.",
            "tools": ["bash", "environment_check"],
            "capabilities": ["tool.recovery", "mcp.github"],
            "artifacts": ["build_logs", "deployment_notes"],
        },
        "security": {
            "name": "Security",
            "role": "security",
            "goal": "Review high-risk actions, secrets, permission boundaries, and data safety.",
            "tools": ["read_file", "search_codebase", "git_diff"],
            "capabilities": ["tool.project_index", "skill.delivery-review"],
            "artifacts": ["security_risks", "permission_notes"],
        },
        "migration": {
            "name": "Migration",
            "role": "migration",
            "goal": "Assess data/schema/config migration risk and backward compatibility.",
            "tools": ["read_file", "search_codebase", "project_context"],
            "capabilities": ["tool.project_index", "tool.recovery"],
            "artifacts": ["migration_plan", "rollback_notes"],
        },
    }
)


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _conversation_root(workspace_dir: str | None = None) -> Path:
    root = _workspace(workspace_dir) / ".nanocursor" / "conversations"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw.strip()).strip("-")
    return safe[:120] or f"conversation-{uuid.uuid4()}"


def _conversation_dir(conversation_id: str, workspace_dir: str | None = None) -> Path:
    path = _conversation_root(workspace_dir) / _safe_id(conversation_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conversation_path(conversation_id: str, workspace_dir: str | None = None) -> Path:
    return _conversation_dir(conversation_id, workspace_dir) / "conversation.json"


def _team_path(conversation_id: str, workspace_dir: str | None = None) -> Path:
    return _conversation_dir(conversation_id, workspace_dir) / "team.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _public_conversation(conversation: dict[str, Any], workspace_dir: str | None = None) -> dict[str, Any]:
    """Attach derived fields that the frontend can consume directly."""
    run_records = conversation.get("run_records") if isinstance(conversation.get("run_records"), list) else []
    conversation["run_records"] = run_records
    conversation["run_count"] = len(run_records)
    conversation["latest_run"] = run_records[-1] if run_records else None
    team = _read_json(_team_path(conversation["conversation_id"], workspace_dir))
    conversation["team"] = team or {
        "conversation_id": conversation["conversation_id"],
        "members": [],
        "source": "missing",
    }
    return conversation


def _title_from_prompt(prompt: str) -> str:
    text = " ".join((prompt or "").strip().split())
    if not text:
        return "新会话"
    return text[:28] + ("..." if len(text) > 28 else "")


def _is_placeholder_title(title: str | None) -> bool:
    return not str(title or "").strip() or str(title).strip() == "新会话"


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _build_conversation_summary(conversation: dict[str, Any]) -> str:
    """Build a compact, deterministic summary for future runs in this conversation."""
    memory = _build_conversation_memory(conversation)
    return _render_conversation_summary(memory)


def _build_conversation_memory(conversation: dict[str, Any]) -> dict[str, Any]:
    """Build structured deterministic memory for long-running conversations."""
    records = conversation.get("run_records") if isinstance(conversation.get("run_records"), list) else []
    title = str(conversation.get("title") or "")
    prompt = str(conversation.get("prompt") or "")
    if not records:
        summary = " ".join(prompt.split())[:800]
        return {
            "schema_version": 1,
            "title": title or _title_from_prompt(prompt),
            "root_prompt": prompt[:500],
            "summary": summary,
            "recent_runs": [],
            "stable_facts": _unique([summary] if summary else []),
            "open_questions": [],
            "changed_files": [],
            "status_counts": {},
            "agent_roles": [],
            "run_count": 0,
            "token_estimate": max(1, len(summary) // 3) if summary else 0,
            "generated_at": time.time(),
        }

    recent_runs: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    changed_files: list[str] = []
    open_questions: list[str] = []
    agent_roles: list[str] = []
    stable_facts: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status", "unknown") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        record_prompt = " ".join(str(record.get("prompt", "") or "").split())[:180]
        record_summary = " ".join(str(record.get("summary", "") or record.get("error", "") or "").split())[:360]
        for file_path in _extract_file_mentions(f"{record_prompt} {record_summary}"):
            changed_files.append(file_path)
        for member in record.get("team", []) if isinstance(record.get("team"), list) else []:
            if isinstance(member, dict):
                agent_roles.append(str(member.get("role") or member.get("name") or ""))
        if status in {"failed", "blocked", "cancelled", "canceled"} or record.get("error"):
            open_questions.append(record_summary or record_prompt)
        if status in {"completed", "passed", "success"} and record_summary:
            stable_facts.append(record_summary)
        recent_runs.append(
            {
                "thread_id": record.get("thread_id"),
                "run_index": record.get("run_index"),
                "status": status,
                "prompt": record_prompt,
                "summary": record_summary,
                "started_at": record.get("started_at"),
                "completed_at": record.get("completed_at"),
            }
        )

    summary_lines: list[str] = []
    if title or prompt:
        summary_lines.append(f"Conversation: {title or _title_from_prompt(prompt)}")
    if stable_facts:
        summary_lines.append("Stable facts: " + " | ".join(_unique(stable_facts)[-4:]))
    if recent_runs:
        summary_lines.append("Recent runs:")
        for item in recent_runs[-6:]:
            summary_lines.append(
                f"- Run#{item.get('run_index', '?')} [{item.get('status', 'unknown')}] "
                f"{item.get('prompt', '')[:90]} -> {item.get('summary', '')[:180]}"
            )
    if changed_files:
        summary_lines.append("Likely files: " + ", ".join(_unique(changed_files)[-12:]))
    if open_questions:
        summary_lines.append("Open questions/risks: " + " | ".join(_unique(open_questions)[-4:]))

    summary = "\n".join(line for line in summary_lines if line).strip()[:1800]
    return {
        "schema_version": 1,
        "title": title or _title_from_prompt(prompt),
        "root_prompt": prompt[:500],
        "summary": summary,
        "recent_runs": recent_runs[-8:],
        "stable_facts": _unique(stable_facts)[-8:],
        "open_questions": _unique(open_questions)[-8:],
        "changed_files": _unique(changed_files)[-24:],
        "status_counts": status_counts,
        "agent_roles": _unique(agent_roles),
        "run_count": len(recent_runs),
        "token_estimate": max(1, len(summary) // 3) if summary else 0,
        "generated_at": time.time(),
    }


def _render_conversation_summary(memory: dict[str, Any]) -> str:
    summary = str(memory.get("summary") or "")
    if summary:
        return summary[:1800]
    root_prompt = str(memory.get("root_prompt") or "")
    return root_prompt[:800]


def _extract_file_mentions(text: str) -> list[str]:
    matches = re.findall(
        r"(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|[\w.-]+\.(?:py|js|jsx|ts|tsx|css|html|md|json|yaml|yml|toml)",
        text or "",
    )
    return _unique(matches)[:40]


def refresh_conversation_memory(
    conversation_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Refresh structured conversation memory and compact summary."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        raise ValueError(f"会话不存在: {conversation_id}")
    memory = _build_conversation_memory(conversation)
    changes = {
        "conversation_memory": memory,
        "conversation_summary": _render_conversation_summary(memory),
        "summary_compacted_at": memory.get("generated_at"),
        "summary_stats": {
            "run_count": memory.get("run_count", 0),
            "token_estimate": memory.get("token_estimate", 0),
            "changed_file_count": len(memory.get("changed_files", [])),
            "open_question_count": len(memory.get("open_questions", [])),
        },
    }
    touch_conversation(conversation_id, workspace_dir, **changes)
    return get_conversation(conversation_id, workspace_dir) or {**conversation, **changes}


def get_conversation_memory(
    conversation_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Return structured conversation memory, refreshing it if absent."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        raise ValueError(f"会话不存在: {conversation_id}")
    memory = conversation.get("conversation_memory")
    if not isinstance(memory, dict):
        conversation = refresh_conversation_memory(conversation_id, workspace_dir)
        memory = conversation.get("conversation_memory")
    return {
        "conversation_id": conversation_id,
        "workspace_dir": conversation.get("workspace_dir"),
        "conversation_summary": conversation.get("conversation_summary", ""),
        "conversation_memory": memory if isinstance(memory, dict) else {},
        "summary_stats": conversation.get("summary_stats", {}),
    }


def _capability_ids_for_agent(agent_name: str, recommendation: dict[str, Any]) -> list[str]:
    preset = AGENT_PRESETS.get(agent_name.lower(), {})
    preset_ids = [str(item) for item in preset.get("capabilities", [])]
    matched_ids: list[str] = []
    for capability in recommendation.get("capabilities", []):
        capability_id = str(capability.get("id", "")).strip()
        capability_agents = [str(item).lower() for item in capability.get("agents", [])]
        if capability_id and agent_name.lower() in capability_agents:
            matched_ids.append(capability_id)
    if not matched_ids and preset_ids:
        matched_ids = preset_ids
    if not matched_ids:
        matched_ids = [
            str(item.get("id"))
            for item in recommendation.get("capabilities", [])[:2]
            if item.get("id")
        ]
    return _unique([*preset_ids, *matched_ids])[:6]


def assess_task_complexity(prompt: str) -> dict[str, Any]:
    """Classify how much Agent structure this run actually needs (keyword-only)."""
    intent = classify_user_intent(prompt)
    return {
        "level": "high_risk" if intent["level"] == "high_risk" else intent["level"],
        "rationale": intent["rationale"],
        "indicators": intent["signals"],
        "intent": intent["intent"],
        "route": intent["route"],
        "execution_route": intent["execution_route"],
        "intent_decision": intent,
        "requires_workspace_write": intent["requires_workspace_write"],
        "requires_workspace_read": intent["requires_workspace_read"],
        "requires_shell": intent["requires_shell"],
        "requires_approval": intent["requires_approval"],
        "requires_execution": intent["requires_execution"],
        "confidence": intent["confidence"],
    }


async def assess_task_complexity_async(prompt: str) -> dict[str, Any]:
    """Classify task complexity with LLM assistance."""
    from src.api.services.intent_router import classify_user_intent_async
    intent = await classify_user_intent_async(prompt)
    return {
        "level": "high_risk" if intent["level"] == "high_risk" else intent["level"],
        "rationale": intent["rationale"],
        "indicators": intent["signals"],
        "intent": intent["intent"],
        "route": intent["route"],
        "execution_route": intent["execution_route"],
        "intent_decision": intent,
        "requires_workspace_write": intent["requires_workspace_write"],
        "requires_workspace_read": intent["requires_workspace_read"],
        "requires_shell": intent["requires_shell"],
        "requires_approval": intent["requires_approval"],
        "requires_execution": intent["requires_execution"],
        "confidence": intent["confidence"],
    }


def compose_runtime_team(
    prompt: str,
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Compose a minimal runtime team from task complexity (keyword-only)."""
    complexity = assess_task_complexity(prompt)
    return _build_team_from_complexity(prompt, complexity, workspace_dir, conversation_id)


async def compose_runtime_team_async(
    prompt: str,
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Compose a minimal runtime team with LLM-assisted classification."""
    complexity = await assess_task_complexity_async(prompt)
    return _build_team_from_complexity(prompt, complexity, workspace_dir, conversation_id)


def _build_team_from_complexity(
    prompt: str,
    complexity: dict[str, Any],
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Build team members from a complexity assessment."""
    level = complexity["level"]
    roles_by_level = {
        "simple": ["Lead"],
        "small_code": ["Lead", "Coder"],
        "medium": ["Lead", "Planner", "Coder", "Reviewer"],
        "high_risk": ["Lead", "Planner", "Coder", "Reviewer", "Tester"],
    }
    agent_names = list(roles_by_level.get(level, ["Lead"]))
    text = str(prompt or "").lower()
    if level == "high_risk" and any(word in text for word in ["安全", "权限", "认证", "鉴权", "secret", "token"]):
        agent_names.append("Security")
    if level == "high_risk" and any(word in text for word in ["迁移", "数据库", "schema", "兼容", "回滚"]):
        agent_names.append("Migration")

    should_recommend = level != "simple" or complexity.get("execution_route") != "lead_direct_reply"
    recommendation = recommend_capabilities(prompt, str(_workspace(workspace_dir))) if should_recommend else {
        "agents": ["Lead"],
        "capabilities": [],
        "mcp_plan": [],
        "reasons": [],
    }
    if level == "simple" and complexity.get("execution_route") != "lead_direct_reply":
        intent_decision = complexity.get("intent_decision")
        suggested_agents = (
            intent_decision.get("suggested_agents", [])
            if isinstance(intent_decision, dict)
            else []
        )
        agent_names = _unique(["Lead", *[str(agent) for agent in suggested_agents]])
    recommendation["agents"] = agent_names

    members = [_member_from_agent(name, recommendation, source="runtime_composed") for name in agent_names]
    for member in members:
        member["lifetime"] = "run_snapshot"
        member["last_action"] = "按本轮任务复杂度临时加入；运行结束后不会写入永久团队。"

    return {
        "conversation_id": conversation_id,
        "prompt": prompt,
        "complexity": complexity,
        "members": members,
        "capabilities": recommendation.get("capabilities", []),
        "mcp_plan": recommendation.get("mcp_plan", []),
        "reasons": [
            complexity["rationale"],
            "Agent 数量按需收敛：简单任务少 Agent，高风险任务才增加复核角色。",
        ],
        "summary": {
            "agent_count": len(members),
            "complexity_level": level,
            "persistent": False,
            "temporary": True,
        },
    }


def _member_from_agent(agent_name: str, recommendation: dict[str, Any], source: str) -> dict[str, Any]:
    preset = AGENT_PRESETS.get(agent_name.lower(), {})
    name = str(preset.get("name") or agent_name).strip() or "Agent"
    role = str(preset.get("role") or agent_name).strip().lower().replace(" ", "_")
    return {
        "id": role,
        "name": name,
        "role": role,
        "status": "idle",
        "goal": preset.get("goal") or f"Handle the {name} part of this nanoCursor delivery.",
        "tools": [str(item) for item in preset.get("tools", [])],
        "capabilities": _capability_ids_for_agent(name, recommendation),
        "current_task_id": None,
        "last_action": "由 nanoCursor 根据本次需求自动推荐。",
        "artifacts": [str(item) for item in preset.get("artifacts", [])],
        "last_active_at": None,
        "source": source,
    }


def lead_only_team(conversation_id: str | None = None, prompt: str = "") -> dict[str, Any]:
    """Return the minimal starting team: one Lead that can expand later."""
    recommendation = {
        "agents": ["Lead"],
        "capabilities": [],
        "mcp_plan": [],
        "reasons": ["新会话先由 Lead 接收上下文，复杂任务再临时拉起子 Agent。"],
    }
    member = _member_from_agent("Lead", recommendation, source="lead_only")
    member["last_action"] = "等待用户输入；必要时会为任务创建子 Agent。"
    return {
        "conversation_id": conversation_id,
        "prompt": prompt,
        "members": [member],
        "capabilities": [],
        "mcp_plan": [],
        "reasons": recommendation["reasons"],
        "summary": {
            "agent_count": 1,
            "capability_count": 0,
            "ready_count": 0,
            "planned_count": 0,
            "mcp_count": 0,
            "usable_mcp_count": 0,
        },
    }


def recommend_conversation_team(
    prompt: str,
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Recommend a task-specific agent team for a prompt."""
    if not str(prompt or "").strip():
        return lead_only_team(conversation_id, prompt)

    recommendation = recommend_capabilities(prompt, str(_workspace(workspace_dir)))
    agent_names = list(recommendation.get("agents", []))
    if "Lead" not in agent_names:
        agent_names.insert(0, "Lead")

    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for agent_name in agent_names:
        key = str(agent_name).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        members.append(_member_from_agent(str(agent_name), recommendation, source="recommended"))

    ready_count = sum(
        1
        for capability in recommendation.get("capabilities", [])
        if capability.get("status") in {"ready", "configured"}
    )
    return {
        "conversation_id": conversation_id,
        "prompt": prompt,
        "members": members,
        "capabilities": recommendation.get("capabilities", []),
        "mcp_plan": recommendation.get("mcp_plan", []),
        "reasons": recommendation.get("reasons", []),
        "summary": {
            "agent_count": len(members),
            "capability_count": len(recommendation.get("capabilities", [])),
            "ready_count": ready_count,
            "planned_count": len(recommendation.get("capabilities", [])) - ready_count,
            "mcp_count": len(recommendation.get("mcp_plan", [])),
            "usable_mcp_count": sum(1 for item in recommendation.get("mcp_plan", []) if item.get("usable")),
        },
    }


def _normalize_member(raw: dict[str, Any], index: int, source: str = "user") -> dict[str, Any]:
    name = str(raw.get("name") or f"Agent {index + 1}").strip()
    role = str(raw.get("role") or name).strip().lower().replace(" ", "_")
    if not name:
        raise ValueError("Agent 名称不能为空。")
    if not role:
        raise ValueError("Agent 角色不能为空。")
    tools = raw.get("tools") if isinstance(raw.get("tools"), list) else []
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), list) else []
    artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), list) else []
    return {
        "id": str(raw.get("id") or role),
        "name": name,
        "role": role,
        "status": str(raw.get("status") or "idle"),
        "goal": str(raw.get("goal") or raw.get("prompt") or ""),
        "tools": _unique([str(item) for item in tools]),
        "capabilities": _unique([str(item) for item in capabilities]),
        "current_task_id": raw.get("current_task_id"),
        "last_action": str(raw.get("last_action") or "用户调整了本会话团队配置。"),
        "artifacts": _unique([str(item) for item in artifacts]),
        "last_active_at": raw.get("last_active_at"),
        "source": source,
    }


def _persist_team(
    conversation_id: str,
    members: list[dict[str, Any]],
    workspace_dir: str | None = None,
    source: str = "user",
    recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    team = {
        "conversation_id": conversation_id,
        "source": source,
        "members": members,
        "recommendation": recommendation or {},
        "updated_at": now,
    }
    _write_json(_team_path(conversation_id, workspace_dir), team)
    return team


def create_conversation(prompt: str = "", workspace_dir: str | None = None) -> dict[str, Any]:
    """Create an isolated nanoCursor conversation context."""
    workspace = _workspace(workspace_dir)
    conversation_id = f"conv-{uuid.uuid4()}"
    now = time.time()
    conversation = {
        "conversation_id": conversation_id,
        "workspace_dir": str(workspace),
        "title": _title_from_prompt(prompt),
        "prompt": prompt,
        "status": "draft",
        "agent_loop_policy": "run_per_message",
        "current_thread_id": None,
        "run_ids": [],
        "run_records": [],
        "conversation_summary": "",
        "created_at": now,
        "updated_at": now,
    }
    _write_json(_conversation_path(conversation_id, str(workspace)), conversation)

    recommendation = lead_only_team(conversation_id, prompt)
    _persist_team(
        conversation_id,
        recommendation["members"],
        str(workspace),
        source="lead_only",
        recommendation=recommendation,
    )
    return get_conversation(conversation_id, str(workspace)) or conversation


def get_conversation(conversation_id: str, workspace_dir: str | None = None) -> dict[str, Any] | None:
    """Load one conversation with its current team."""
    conversation = _read_json(_conversation_path(conversation_id, workspace_dir))
    if not conversation:
        return None
    return _public_conversation(conversation, workspace_dir)


def list_conversations(workspace_dir: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List workspace conversations newest first."""
    root = _conversation_root(workspace_dir)
    conversations: list[dict[str, Any]] = []
    for path in root.glob("*/conversation.json"):
        conversation = _read_json(path)
        if conversation:
            team = _read_json(path.parent / "team.json") or {}
            run_records = conversation.get("run_records") if isinstance(conversation.get("run_records"), list) else []
            conversation["team_summary"] = {
                "agent_count": len(team.get("members", [])),
                "source": team.get("source", "unknown"),
            }
            conversation["run_count"] = len(run_records)
            conversation["latest_run"] = run_records[-1] if run_records else None
            conversations.append(conversation)
    conversations.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return conversations[: max(0, min(limit, 200))]


def list_conversation_runs(
    conversation_id: str,
    workspace_dir: str | None = None,
    limit: int = 50,
) -> dict[str, Any] | None:
    """Return run records scoped to one conversation without listing workspace-global runs."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        return None

    records = conversation.get("run_records") if isinstance(conversation.get("run_records"), list) else []
    records = [record for record in records if isinstance(record, dict)]
    records.sort(key=lambda item: item.get("updated_at") or item.get("started_at") or 0, reverse=True)
    safe_limit = max(0, min(limit, 200))
    return {
        "conversation_id": conversation_id,
        "workspace_dir": conversation.get("workspace_dir") or str(_workspace(workspace_dir)),
        "current_thread_id": conversation.get("current_thread_id"),
        "run_count": len(records),
        "runs": records[:safe_limit],
    }


def update_conversation_team(
    conversation_id: str,
    members: list[dict[str, Any]],
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Replace the editable team for a conversation."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        raise ValueError("未找到该会话。")
    normalized = [_normalize_member(member, index, source="user") for index, member in enumerate(members)]
    if not normalized:
        raise ValueError("会话团队至少需要一个 Agent。")
    team = _persist_team(conversation_id, normalized, workspace_dir, source="user")
    touch_conversation(conversation_id, workspace_dir, status=conversation.get("status", "draft"))
    return team


def refresh_conversation_recommendation(
    conversation_id: str,
    prompt: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Regenerate and persist the recommended team for a conversation."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        raise ValueError("未找到该会话。")
    recommendation = recommend_conversation_team(prompt, workspace_dir, conversation_id)
    team = _persist_team(
        conversation_id,
        recommendation["members"],
        workspace_dir,
        source="recommended",
        recommendation=recommendation,
    )
    touch_conversation(
        conversation_id,
        workspace_dir,
        prompt=prompt,
        title=_title_from_prompt(prompt),
        status=conversation.get("status", "draft"),
    )
    return {"recommendation": recommendation, "team": team}


def touch_conversation(
    conversation_id: str,
    workspace_dir: str | None = None,
    **changes: Any,
) -> dict[str, Any] | None:
    """Update conversation metadata."""
    path = _conversation_path(conversation_id, workspace_dir)
    conversation = _read_json(path)
    if not conversation:
        return None
    conversation.update(changes)
    conversation["updated_at"] = time.time()
    _write_json(path, conversation)
    return conversation


def link_run_to_conversation(
    conversation_id: str,
    thread_id: str,
    workspace_dir: str | None = None,
    prompt: str = "",
    team: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach a newly started run to its conversation."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        raise ValueError("未找到该会话。")
    run_ids = list(conversation.get("run_ids", []))
    if thread_id not in run_ids:
        run_ids.append(thread_id)
    now = time.time()
    run_records = conversation.get("run_records") if isinstance(conversation.get("run_records"), list) else []
    existing = next((record for record in run_records if record.get("thread_id") == thread_id), None)
    run_index = len(run_records) + 1 if existing is None else existing.get("run_index", len(run_records))
    record = {
        "thread_id": thread_id,
        "run_index": run_index,
        "prompt": prompt or conversation.get("prompt", ""),
        "status": "running",
        "team": team or [],
        "started_at": existing.get("started_at") if existing else now,
        "updated_at": now,
        "completed_at": None,
        "summary": "",
        "error": "",
    }
    if existing is None:
        run_records.append(record)
    else:
        existing.update(record)
    first_run = len(run_records) == 1 and existing is None
    conversation_title = conversation.get("title")
    conversation_prompt = conversation.get("prompt", "")
    metadata_updates = {
        "status": "running",
        "current_thread_id": thread_id,
        "run_ids": run_ids,
        "run_records": run_records,
        "latest_prompt": prompt or conversation_prompt,
    }
    if first_run or _is_placeholder_title(conversation_title):
        metadata_updates["title"] = _title_from_prompt(prompt or conversation_prompt)
    if not str(conversation_prompt or "").strip():
        metadata_updates["prompt"] = prompt or conversation_prompt

    updated = touch_conversation(
        conversation_id,
        workspace_dir,
        **metadata_updates,
    )
    return get_conversation(conversation_id, workspace_dir) or updated or {}


def finalize_conversation_run(
    conversation_id: str,
    thread_id: str,
    status: str,
    workspace_dir: str | None = None,
    summary: str = "",
    error: str = "",
) -> dict[str, Any] | None:
    """Mark a conversation run as terminal and sync the conversation status."""
    conversation = get_conversation(conversation_id, workspace_dir)
    if not conversation:
        return None
    now = time.time()
    run_records = conversation.get("run_records") if isinstance(conversation.get("run_records"), list) else []
    record = next((item for item in run_records if item.get("thread_id") == thread_id), None)
    if record is None:
        record = {
            "thread_id": thread_id,
            "run_index": len(run_records) + 1,
            "prompt": conversation.get("prompt", ""),
            "team": [],
            "started_at": now,
        }
        run_records.append(record)

    record.update(
        {
            "status": status,
            "updated_at": now,
            "completed_at": now,
            "summary": summary[:500],
            "error": error[:500],
        }
    )

    changes: dict[str, Any] = {
        "run_records": run_records,
        "last_run_status": status,
        "last_run_summary": summary[:500],
    }
    summary_seed = {**conversation, "run_records": run_records}
    memory = _build_conversation_memory(summary_seed)
    changes["conversation_memory"] = memory
    changes["conversation_summary"] = _render_conversation_summary(memory)
    changes["summary_compacted_at"] = memory.get("generated_at")
    changes["summary_stats"] = {
        "run_count": memory.get("run_count", 0),
        "token_estimate": memory.get("token_estimate", 0),
        "changed_file_count": len(memory.get("changed_files", [])),
        "open_question_count": len(memory.get("open_questions", [])),
    }
    if conversation.get("current_thread_id") == thread_id:
        changes["status"] = status

    touch_conversation(conversation_id, workspace_dir, **changes)
    return get_conversation(conversation_id, workspace_dir)
