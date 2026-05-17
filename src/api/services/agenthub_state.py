"""Read AgentHub task and team state from workspace files."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module


DEFAULT_TEAM = [
    {
        "name": "Lead",
        "role": "lead",
        "status": "idle",
        "goal": "Coordinate the delivery flow and keep the run outcome aligned with the user request.",
        "tools": ["plan", "delegate", "report"],
        "capabilities": [],
        "current_task_id": None,
        "last_action": "",
        "artifacts": [],
        "last_active_at": None,
        "source": "default",
    },
    {
        "name": "Planner",
        "role": "planner",
        "status": "idle",
        "goal": "Break the request into tasks, dependencies, and acceptance criteria.",
        "tools": ["task_create", "task_update"],
        "capabilities": ["tool.project_index"],
        "current_task_id": None,
        "last_action": "",
        "artifacts": ["tasks"],
        "last_active_at": None,
        "source": "default",
    },
    {
        "name": "Coder",
        "role": "coder",
        "status": "idle",
        "goal": "Implement code changes and keep file edits traceable.",
        "tools": ["write_file", "edit_file", "bash"],
        "capabilities": ["tool.file_ops", "tool.project_index"],
        "current_task_id": None,
        "last_action": "",
        "artifacts": ["changed_files", "diff_patch"],
        "last_active_at": None,
        "source": "default",
    },
    {
        "name": "Tester",
        "role": "tester",
        "status": "idle",
        "goal": "Verify the delivery against acceptance criteria and surface risks.",
        "tools": ["bash", "report"],
        "capabilities": ["tool.recovery", "skill.delivery-review"],
        "current_task_id": None,
        "last_action": "",
        "artifacts": ["tests", "quality"],
        "last_active_at": None,
        "source": "default",
    },
]


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _normalize_status(status: str | None) -> str:
    if not status:
        return "pending"
    aliases = {
        "working": "in_progress",
        "running": "in_progress",
        "done": "completed",
        "complete": "completed",
        "error": "failed",
    }
    return aliases.get(status, status)


def infer_task_capabilities(
    title: str,
    description: str = "",
    owner: str | None = None,
    explicit: list[str] | None = None,
) -> list[str]:
    """Infer capability ids for a task from owner and task text."""
    capabilities: list[str] = []

    def add(item: str) -> None:
        if item and item not in capabilities:
            capabilities.append(item)

    for item in explicit or []:
        add(str(item).strip())

    owner_text = str(owner or "").lower()
    text = f"{title} {description}".lower()

    if "planner" in owner_text or any(keyword in text for keyword in ["需求", "验收", "计划", "拆解", "文档", "接口"]):
        add("tool.project_index")
    if "coder" in owner_text or any(keyword in text for keyword in ["实现", "代码", "界面", "本地存储", "文件", "样式"]):
        add("tool.file_ops")
        add("tool.project_index")
    if any(keyword in text for keyword in ["界面", "前端", "ui", "样式", "布局", "交互"]):
        add("skill.frontend-polish")
    if "tester" in owner_text or any(keyword in text for keyword in ["测试", "验证", "质量", "报告", "复核"]):
        add("skill.delivery-review")
        add("tool.recovery")
    if any(keyword in text for keyword in ["偏好", "风格", "记住"]):
        add("tool.memory")

    return capabilities[:5]


def _team_path(workspace_dir: str | None = None) -> Path:
    return _workspace(workspace_dir) / ".team" / "config.json"


def _normalize_member(raw: dict[str, Any], source: str, now: float | None = None) -> dict[str, Any]:
    role = str(raw.get("role") or "agent").strip() or "agent"
    tools = raw.get("tools") if isinstance(raw.get("tools"), list) else []
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), list) else []
    artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), list) else []
    return {
        "name": str(raw.get("name") or "Agent").strip() or "Agent",
        "role": role,
        "status": raw.get("status", "idle"),
        "goal": raw.get("goal") or raw.get("prompt") or "",
        "tools": [str(tool) for tool in tools if tool],
        "capabilities": [str(item) for item in capabilities if item],
        "current_task_id": raw.get("current_task_id"),
        "last_action": raw.get("last_action") or "",
        "artifacts": [str(item) for item in artifacts if item],
        "last_active_at": raw.get("last_active_at") or raw.get("updated_at") or now,
        "source": source,
    }


def list_task_items(workspace_dir: str | None = None) -> list[dict[str, Any]]:
    """Return normalized tasks from workspace/.tasks/task_*.json."""
    tasks_dir = _workspace(workspace_dir) / ".tasks"
    if not tasks_dir.exists():
        return []

    tasks: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("task_*.json")):
        raw = _read_json(path)
        if not raw:
            continue

        task_id = str(raw.get("id") or path.stem.replace("task_", ""))
        title = raw.get("title") or raw.get("subject") or raw.get("description") or task_id
        description = raw.get("description") or raw.get("subject") or ""
        dependencies = raw.get("dependencies") or raw.get("blocked_by") or raw.get("blockedBy") or []
        owner = raw.get("owner") or raw.get("assignee")
        raw_capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), list) else []

        tasks.append(
            {
                "id": task_id,
                "title": title,
                "description": description,
                "status": _normalize_status(raw.get("status")),
                "owner": owner,
                "capabilities": infer_task_capabilities(title, description, owner, raw_capabilities),
                "dependencies": dependencies,
                "result": raw.get("result") or raw.get("output") or "",
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at") or raw.get("completed_at") or raw.get("claimed_at"),
            }
        )

    return sorted(tasks, key=lambda item: item.get("created_at") or 0)


def list_team_members(workspace_dir: str | None = None) -> list[dict[str, Any]]:
    """Return normalized team members from workspace/.team/config.json."""
    team_path = _team_path(workspace_dir)
    config = _read_json(team_path) if team_path.exists() else None

    if not config or not config.get("members"):
        return [dict(member) for member in DEFAULT_TEAM]

    now = time.time()
    return [_normalize_member(raw, "workspace", now=now) for raw in config.get("members", [])]


def add_team_member(
    name: str,
    role: str,
    goal: str = "",
    tools: list[str] | None = None,
    capabilities: list[str] | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Add a custom Agent role card to workspace/.team/config.json."""
    workspace = _workspace(workspace_dir)
    team_path = _team_path(str(workspace))
    team_path.parent.mkdir(parents=True, exist_ok=True)

    config = _read_json(team_path) if team_path.exists() else None
    if not config:
        config = {"team_name": "agenthub-custom", "members": [dict(member) for member in DEFAULT_TEAM]}

    members = config.setdefault("members", [])
    normalized_name = name.strip()
    normalized_role = role.strip().lower().replace(" ", "_")
    if not normalized_name:
        raise ValueError("Agent name is required.")
    if not normalized_role:
        raise ValueError("Agent role is required.")

    if any(str(member.get("name", "")).lower() == normalized_name.lower() for member in members):
        raise ValueError(f"Agent '{normalized_name}' already exists.")

    now = time.time()
    member = {
        "name": normalized_name,
        "role": normalized_role,
        "status": "idle",
        "goal": goal.strip(),
        "tools": [str(tool).strip() for tool in (tools or []) if str(tool).strip()],
        "capabilities": [str(item).strip() for item in (capabilities or []) if str(item).strip()],
        "current_task_id": None,
        "last_action": "用户创建了自定义 Agent 角色卡。",
        "artifacts": [],
        "last_active_at": now,
        "updated_at": now,
    }
    members.append(member)

    team_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return _normalize_member(member, "workspace", now=now)
