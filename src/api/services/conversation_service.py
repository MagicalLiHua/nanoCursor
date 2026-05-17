"""Conversation-scoped orchestration for AgentHub."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from src.api.services.agenthub_state import DEFAULT_TEAM
from src.api.services.capability_service import recommend_capabilities
from src.infra import config as config_module


AGENT_PRESETS: dict[str, dict[str, Any]] = {
    member["name"].lower(): member for member in DEFAULT_TEAM
}

AGENT_PRESETS.update(
    {
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


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


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


def _member_from_agent(agent_name: str, recommendation: dict[str, Any], source: str) -> dict[str, Any]:
    preset = AGENT_PRESETS.get(agent_name.lower(), {})
    name = str(preset.get("name") or agent_name).strip() or "Agent"
    role = str(preset.get("role") or agent_name).strip().lower().replace(" ", "_")
    return {
        "id": role,
        "name": name,
        "role": role,
        "status": "idle",
        "goal": preset.get("goal") or f"Handle the {name} part of this AgentHub delivery.",
        "tools": [str(item) for item in preset.get("tools", [])],
        "capabilities": _capability_ids_for_agent(name, recommendation),
        "current_task_id": None,
        "last_action": "由 AgentHub 根据本次需求自动推荐。",
        "artifacts": [str(item) for item in preset.get("artifacts", [])],
        "last_active_at": None,
        "source": source,
    }


def recommend_conversation_team(
    prompt: str,
    workspace_dir: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Recommend a task-specific agent team for a prompt."""
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
        "reasons": recommendation.get("reasons", []),
        "summary": {
            "agent_count": len(members),
            "capability_count": len(recommendation.get("capabilities", [])),
            "ready_count": ready_count,
            "planned_count": len(recommendation.get("capabilities", [])) - ready_count,
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
    """Create an isolated AgentHub conversation context."""
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
        "created_at": now,
        "updated_at": now,
    }
    _write_json(_conversation_path(conversation_id, str(workspace)), conversation)

    recommendation = recommend_conversation_team(prompt, str(workspace), conversation_id)
    _persist_team(
        conversation_id,
        recommendation["members"],
        str(workspace),
        source="recommended",
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
    updated = touch_conversation(
        conversation_id,
        workspace_dir,
        status="running",
        prompt=prompt or conversation.get("prompt", ""),
        title=_title_from_prompt(prompt or conversation.get("prompt", "")),
        current_thread_id=thread_id,
        run_ids=run_ids,
        run_records=run_records,
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
    if conversation.get("current_thread_id") == thread_id:
        changes["status"] = status

    touch_conversation(conversation_id, workspace_dir, **changes)
    return get_conversation(conversation_id, workspace_dir)
