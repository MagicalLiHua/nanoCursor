"""Runtime context used by semantic intent routing.

The intent router should not decide from the user prompt alone. Short follow-up
messages such as "继续" need conversation/run state, while safety-sensitive
messages need pending approval and recent failure signals. This module keeps the
context shape explicit and intentionally small so it can be passed to an LLM
classifier without leaking the entire project.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class IntentRuntimeContext(BaseModel):
    """Compact runtime state for intent classification."""

    conversation_id: str = ""
    thread_id: str = ""
    workspace_dir: str = ""
    conversation_summary: str = ""
    last_user_message: str = ""
    last_assistant_message: str = ""
    last_intent_route: str = ""
    active_run_status: str = ""
    active_run_stage: str = ""
    last_tool_name: str = ""
    last_tool_status: str = ""
    last_error_type: str = ""
    has_pending_approval: bool = False
    has_uncommitted_diff: bool = False
    changed_file_count: int = 0
    selected_files: list[str] = Field(default_factory=list)
    recent_files: list[str] = Field(default_factory=list)
    workspace_is_git: bool = False

    def compact_for_prompt(self) -> dict[str, Any]:
        """Return a bounded, JSON-serializable context for classification."""

        return {
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "workspace_dir": self.workspace_dir,
            "conversation_summary": self.conversation_summary[:1200],
            "last_user_message": self.last_user_message[:500],
            "last_assistant_message": self.last_assistant_message[:500],
            "last_intent_route": self.last_intent_route,
            "active_run_status": self.active_run_status,
            "active_run_stage": self.active_run_stage,
            "last_tool_name": self.last_tool_name,
            "last_tool_status": self.last_tool_status,
            "last_error_type": self.last_error_type,
            "has_pending_approval": self.has_pending_approval,
            "has_uncommitted_diff": self.has_uncommitted_diff,
            "changed_file_count": self.changed_file_count,
            "selected_files": self.selected_files[:12],
            "recent_files": self.recent_files[:12],
            "workspace_is_git": self.workspace_is_git,
        }


def coerce_intent_runtime_context(
    value: IntentRuntimeContext | dict[str, Any] | None = None,
    *,
    conversation_summary: str = "",
    workspace_dir: str = "",
) -> IntentRuntimeContext:
    """Coerce user-provided context into ``IntentRuntimeContext``."""

    if isinstance(value, IntentRuntimeContext):
        context = value
    elif isinstance(value, dict):
        context = IntentRuntimeContext.model_validate(value)
    else:
        context = IntentRuntimeContext()

    updates: dict[str, Any] = {}
    if conversation_summary and not context.conversation_summary:
        updates["conversation_summary"] = conversation_summary
    if workspace_dir and not context.workspace_dir:
        updates["workspace_dir"] = workspace_dir
    return context.model_copy(update=updates) if updates else context


def context_from_conversation(
    conversation: dict[str, Any] | None,
    *,
    prompt: str = "",
    workspace_dir: str = "",
) -> IntentRuntimeContext:
    """Build minimal intent context from a persisted conversation dictionary."""

    data = conversation if isinstance(conversation, dict) else {}
    runs = _conversation_runs(data)
    last_run = _active_or_latest_run(data, runs)
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    last_user = ""
    last_assistant = ""
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        content = str(item.get("content") or "")
        if role == "user" and not last_user:
            last_user = content
        elif role == "assistant" and not last_assistant:
            last_assistant = content
        if last_user and last_assistant:
            break

    workspace = str(workspace_dir or data.get("workspace_dir") or "")
    thread_id = str(last_run.get("thread_id") or data.get("current_thread_id") or "")
    session = _read_run_session(workspace, thread_id)
    intent = _first_dict(session.get("intent_decision"), session.get("intent_decision_normalized"))
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    if not intent and isinstance(plan.get("intent_decision"), dict):
        intent = plan["intent_decision"]
    memory = data.get("conversation_memory") if isinstance(data.get("conversation_memory"), dict) else {}
    summary_stats = data.get("summary_stats") if isinstance(data.get("summary_stats"), dict) else {}
    changed_files = _string_list(memory.get("changed_files"))
    recent_files = _unique([*_string_list(last_run.get("changed_files")), *changed_files])[:20]
    active_stage = str(
        session.get("active_stage")
        or session.get("current_stage")
        or plan.get("current_stage")
        or last_run.get("stage")
        or ""
    )
    last_tool = _last_tool_snapshot(session)
    status = str(last_run.get("status") or session.get("status") or data.get("last_run_status") or "")

    return IntentRuntimeContext(
        conversation_id=str(data.get("id") or data.get("conversation_id") or ""),
        thread_id=thread_id,
        workspace_dir=workspace,
        conversation_summary=str(data.get("conversation_summary") or ""),
        last_user_message=last_user if last_user != prompt else "",
        last_assistant_message=last_assistant,
        last_intent_route=str(last_run.get("intent_route") or intent.get("route") or ""),
        active_run_status=status,
        active_run_stage=active_stage,
        last_tool_name=str(last_tool.get("name") or ""),
        last_tool_status=str(last_tool.get("status") or ""),
        last_error_type=str(last_run.get("error_type") or session.get("last_error_type") or ""),
        has_pending_approval=_has_pending_approval(session),
        has_uncommitted_diff=bool(session.get("has_uncommitted_diff") or last_run.get("has_uncommitted_diff")),
        changed_file_count=int(summary_stats.get("changed_file_count") or len(recent_files)),
        selected_files=_string_list(session.get("selected_files"))[:20],
        recent_files=recent_files,
        workspace_is_git=(Path(workspace) / ".git").exists() if workspace else False,
    )


def _conversation_runs(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("run_records")
    if not isinstance(raw, list):
        raw = data.get("runs")
    return [item for item in raw or [] if isinstance(item, dict)]


def _active_or_latest_run(data: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    current_thread_id = str(data.get("current_thread_id") or "")
    if current_thread_id:
        current = next((item for item in reversed(runs) if str(item.get("thread_id") or "") == current_thread_id), None)
        if current:
            return current
    running = next((item for item in reversed(runs) if str(item.get("status") or "") == "running"), None)
    return running or (runs[-1] if runs else {})


def _read_run_session(workspace_dir: str, thread_id: str) -> dict[str, Any]:
    if not workspace_dir or not thread_id:
        return {}
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in thread_id)
    path = Path(workspace_dir).resolve() / ".nanocursor" / "runs" / safe_id / "session.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _last_tool_snapshot(session: dict[str, Any]) -> dict[str, Any]:
    events = session.get("events") if isinstance(session.get("events"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool_name = payload.get("tool") or payload.get("tool_name") or event.get("tool") or event.get("tool_name")
        if tool_name:
            return {
                "name": str(tool_name),
                "status": str(payload.get("status") or event.get("status") or event.get("event_type") or ""),
            }
    return {}


def _has_pending_approval(session: dict[str, Any]) -> bool:
    approvals = session.get("approvals") if isinstance(session.get("approvals"), list) else []
    return any(str(item.get("status") or "").lower() in {"pending", "waiting"} for item in approvals if isinstance(item, dict))
