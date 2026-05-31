"""Convert raw tool calls into nanoCursor domain events."""

from __future__ import annotations

import re
from typing import Any

from src.api.services.agent_state import list_team_members
from src.api.services.diff_service import get_run_diff


CAPABILITY_TRACE_BY_TOOL = {
    "write_file": {
        "capability_id": "tool.file_ops",
        "capability_name": "文件读写",
        "kind": "tool",
        "agent": "Coder",
    },
    "edit_file": {
        "capability_id": "tool.file_ops",
        "capability_name": "文件读写",
        "kind": "tool",
        "agent": "Coder",
    },
    "read_file": {
        "capability_id": "tool.project_index",
        "capability_name": "项目索引",
        "kind": "tool",
        "agent": "Coder",
    },
    "list_directory": {
        "capability_id": "tool.project_index",
        "capability_name": "项目索引",
        "kind": "tool",
        "agent": "Planner",
    },
    "search_codebase": {
        "capability_id": "tool.project_index",
        "capability_name": "项目索引",
        "kind": "tool",
        "agent": "Planner",
    },
    "project_context": {
        "capability_id": "tool.project_index",
        "capability_name": "项目索引",
        "kind": "tool",
        "agent": "Planner",
    },
    "bash": {
        "capability_id": "skill.delivery-review",
        "capability_name": "交付复核 Skill",
        "kind": "skill",
        "agent": "Tester",
    },
    "task_create": {
        "capability_id": "tool.project_index",
        "capability_name": "项目索引",
        "kind": "tool",
        "agent": "Planner",
    },
    "task_update": {
        "capability_id": "tool.project_index",
        "capability_name": "项目索引",
        "kind": "tool",
        "agent": "Lead",
    },
    "add_memory": {
        "capability_id": "tool.memory",
        "capability_name": "偏好记忆",
        "kind": "tool",
        "agent": "Lead",
    },
    "recall_memories": {
        "capability_id": "tool.memory",
        "capability_name": "偏好记忆",
        "kind": "tool",
        "agent": "Planner",
    },
    "spawn_agent": {
        "capability_id": "tool.agent_runtime",
        "capability_name": "动态 Agent 运行时",
        "kind": "tool",
        "agent": "Lead",
    },
}


def capability_trace_for_tool(tool_name: str) -> dict[str, str]:
    """Return a display-ready capability trace for a raw tool call."""
    trace = CAPABILITY_TRACE_BY_TOOL.get(tool_name)
    if trace:
        return {**trace, "tool": tool_name}
    return {
        "capability_id": "tool.generic",
        "capability_name": "通用工具",
        "kind": "tool",
        "agent": "Lead",
        "tool": tool_name,
    }


def _created_task_id(output: str) -> str | None:
    match = re.search(r"Created task\s+([^:\s]+)", output)
    return match.group(1) if match else None


def _updated_task_id(output: str) -> str | None:
    match = re.search(r"Updated task\s+([^\s]+)\s+to", output)
    return match.group(1) if match else None


def derive_agenthub_events(
    tool_name: str,
    tool_input: dict[str, Any],
    output: str,
    workspace_dir: str,
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return additional domain events caused by a completed tool call."""
    events: list[dict[str, Any]] = []

    if output.startswith("Error:"):
        return events

    if tool_name == "task_create":
        task_id = _created_task_id(output) or str(tool_input.get("id") or "")
        title = str(tool_input.get("subject") or "新任务")
        description = str(tool_input.get("description") or "")
        dependencies = tool_input.get("blocked_by") or []
        task = {
            "id": task_id,
            "title": title,
            "description": description,
            "status": "pending",
            "owner": tool_input.get("owner") or "Planner",
            "dependencies": dependencies,
            "result": "",
        }
        events.append(
            {
                "event_type": "task_created",
                "title": f"创建任务：{title}",
                "content": description or title,
                "agent": "planner",
                "payload": {"task_id": task_id, "task": task},
            }
        )

    elif tool_name == "task_update":
        task_id = str(tool_input.get("task_id") or _updated_task_id(output) or "")
        status = str(tool_input.get("status") or "pending")
        events.append(
            {
                "event_type": "task_updated",
                "title": f"更新任务：{task_id}",
                "content": f"任务状态变更为 {status}",
                "agent": "lead",
                "payload": {"task_id": task_id, "status": status},
            }
        )

    elif tool_name == "claim_task":
        task_id = str(tool_input.get("task_id") or "")
        events.append(
            {
                "event_type": "task_updated",
                "title": f"认领任务：{task_id}",
                "content": "任务已进入处理中",
                "agent": "lead",
                "payload": {"task_id": task_id, "status": "in_progress"},
            }
        )

    elif tool_name in {"write_file", "edit_file"}:
        path = str(tool_input.get("path") or "")
        if not path:
            return events

        change_type = "modified"
        diff_info: dict[str, Any] | None = None
        if thread_id:
            diff_info = get_run_diff(thread_id, workspace_dir)
            for item in diff_info.get("changed_files", []):
                changed_path = str(item.get("path") or "")
                if changed_path == path or changed_path.endswith(f"/{path}"):
                    change_type = item.get("change_type") or change_type
                    break

        events.append(
            {
                "event_type": "file_changed",
                "title": f"文件变更：{path}",
                "content": output[:1000] if output else "",
                "agent": "coder",
                "payload": {
                    "path": path,
                    "change_type": change_type,
                    "tool": tool_name,
                    "output": output[:2000] if output else "",
                },
            }
        )

        if diff_info is not None:
            changed_files = diff_info.get("changed_files", [])
            events.append(
                {
                    "event_type": "diff_updated",
                    "title": "Diff 已更新",
                    "content": f"{len(changed_files)} 个文件发生变化",
                    "agent": "coder",
                    "payload": {
                        "diff": (diff_info.get("diff") or "")[:50000],
                        "changed_files": changed_files,
                        "source": diff_info.get("source"),
                    },
                }
            )

    elif tool_name in {
        "spawn_teammate",
        "list_teammates",
        "send_message",
        "broadcast",
        "shutdown_request",
        "shutdown_response",
        "plan_approval",
    }:
        members = list_team_members(workspace_dir)
        events.append(
            {
                "event_type": "team_updated",
                "title": "团队状态已更新",
                "content": output[:1000] if output else "",
                "agent": "lead",
                "payload": {"members": members, "tool": tool_name},
            }
        )

    return events
