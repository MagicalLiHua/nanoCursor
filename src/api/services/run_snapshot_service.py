"""Run Snapshot aggregation.

The snapshot is the frontend's read-only, Codex-like overview of one run.  It
collects existing durable records without creating new run artifacts.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from src.api.models import (
    AgentEvent,
    RunSnapshot,
    RunSnapshotActivity,
    RunSnapshotChanges,
    RunSnapshotConversation,
    RunSnapshotQuality,
    RunSnapshotRun,
    RunSnapshotWorkspace,
)
from src.api.services.artifact_service import build_artifact_center
from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.api.services.quality_service import build_quality_gate
from src.api.services.run_outcome_service import build_run_outcome
from src.infra import config as config_module
from src.runtime.task_board import load_task_board


ACTIVE_STATUSES = {"created", "running", "cancelling", "paused"}
ACTIVITY_EVENT_TYPES = {
    "agent_activity",
    "stage_updated",
    "tool_call_finished",
    "file_changed",
    "diff_updated",
    "test_finished",
    "report_ready",
    "ephemeral_agent_spawned",
    "ephemeral_agent_updated",
    "ephemeral_agent_completed",
    "agent_run_started",
    "agent_result_merged",
    "parallel_agent_progress",
    "parallel_agent_result",
}


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _safe_call(default: Any, fn: Callable[[], Any]) -> Any:
    try:
        result = fn()
    except Exception:
        return default
    return result if result is not None else default


def _run_git(workspace: Path, args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode, result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""


def _workspace_snapshot(workspace: Path) -> RunSnapshotWorkspace:
    is_git = _run_git(workspace, ["rev-parse", "--is-inside-work-tree"])[0] == 0
    branch = ""
    dirty = False
    if is_git:
        _, branch = _run_git(workspace, ["rev-parse", "--abbrev-ref", "HEAD"])
        dirty = bool(_run_git(workspace, ["status", "--porcelain"])[1])
    return RunSnapshotWorkspace(
        path=str(workspace),
        name=workspace.name,
        git_branch=branch,
        dirty=dirty,
        is_git_repo=is_git,
    )


def _session_strategy(session: dict[str, Any] | None) -> str:
    plan = session.get("execution_plan") if isinstance(session, dict) else None
    if isinstance(plan, dict):
        return str(plan.get("strategy") or "")
    return ""


def _active_thread_ids() -> set[str]:
    try:
        from src.api.run_state import run_manager

        return {str(item.get("thread_id")) for item in run_manager.list_active() if item.get("thread_id")}
    except Exception:
        return set()


def _event_payload(event: AgentEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _timeline(events: list[AgentEvent], limit: int = 200) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events[-limit:]:
        items.append(
            {
                "id": event.id,
                "type": event.type,
                "timestamp": event.timestamp,
                "agent": event.agent,
                "title": event.title,
                "content": event.content,
                "payload": event.payload,
            }
        )
    return items


def _conversation(session: dict[str, Any] | None, events: list[AgentEvent]) -> RunSnapshotConversation:
    messages: list[dict[str, Any]] = []
    prompt = str((session or {}).get("prompt") or "")
    created_at = (session or {}).get("created_at")
    if prompt:
        messages.append({"role": "user", "content": prompt, "timestamp": created_at})

    for event in events:
        if event.type == "user_message":
            messages.append({"role": "user", "content": event.content, "timestamp": event.timestamp})
        elif event.type == "assistant_message":
            messages.append(
                {
                    "role": "assistant",
                    "agent": event.agent or "lead",
                    "content": event.content,
                    "timestamp": event.timestamp,
                    "payload": event.payload,
                }
            )

    summary = ""
    if isinstance(session, dict):
        summary = str(
            session.get("conversation_summary")
            or session.get("summary")
            or session.get("execution_summary")
            or ""
        )
    return RunSnapshotConversation(
        conversation_id=(session or {}).get("conversation_id") if isinstance(session, dict) else None,
        messages=messages,
        summary=summary,
    )


def _activity_item(event: AgentEvent) -> dict[str, Any]:
    payload = _event_payload(event)
    action = str(payload.get("current_action") or payload.get("action") or event.content or event.title or "")
    status = str(payload.get("status") or "")
    return {
        "id": event.id,
        "type": event.type,
        "timestamp": event.timestamp,
        "agent": event.agent,
        "title": event.title,
        "action": action,
        "status": status,
        "payload": payload,
    }


def _activity(events: list[AgentEvent]) -> RunSnapshotActivity:
    activity_events = [event for event in events if event.type in ACTIVITY_EVENT_TYPES]
    items = [_activity_item(event) for event in activity_events[-20:]]
    current = next((item for item in reversed(items) if item.get("action") or item.get("title")), {})
    return RunSnapshotActivity(
        current_agent=str(current.get("agent") or ""),
        current_action=str(current.get("action") or current.get("title") or ""),
        items=items,
    )


def _diff_line_stats(diff: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            insertions += 1
        elif line.startswith("-"):
            deletions += 1
    return insertions, deletions


def _changes(thread_id: str, workspace: str) -> RunSnapshotChanges:
    diff = _safe_call(
        {"changed_files": [], "diff": "", "source": "missing"},
        lambda: get_run_diff(thread_id, workspace),
    )
    changed_files = diff.get("changed_files") if isinstance(diff, dict) and isinstance(diff.get("changed_files"), list) else []
    insertions, deletions = _diff_line_stats(str(diff.get("diff") or "")) if isinstance(diff, dict) else (0, 0)
    return RunSnapshotChanges(
        files_changed=len(changed_files),
        insertions=insertions,
        deletions=deletions,
        files=changed_files,
        source=str(diff.get("source") or "unknown") if isinstance(diff, dict) else "missing",
    )


def _tasks(thread_id: str, workspace: str) -> list[dict[str, Any]]:
    session = get_event_store().get_session(thread_id, workspace) or {}
    if _session_strategy(session) == "lead_direct_reply":
        return []
    run_dir = Path(workspace).resolve() / ".nanocursor" / "runs" / _safe_run_id(thread_id)
    board = _safe_call(None, lambda: load_task_board(run_dir))
    if not board:
        return []
    return [task.model_dump() for task in board.nodes]


def _safe_run_id(thread_id: str) -> str:
    return thread_id.replace("/", "_").replace("\\", "_")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _agents(thread_id: str, workspace: str) -> list[dict[str, Any]]:
    path = Path(workspace).resolve() / ".nanocursor" / "runs" / _safe_run_id(thread_id) / "ephemeral_agents.json"
    data = _read_json(path)
    if isinstance(data, dict) and isinstance(data.get("agents"), list):
        return [agent for agent in data["agents"] if isinstance(agent, dict)]
    return []


def _artifacts(thread_id: str, workspace: str) -> list[dict[str, Any]]:
    result = _safe_call({"artifacts": []}, lambda: build_artifact_center(thread_id, workspace))
    if isinstance(result, dict) and isinstance(result.get("artifacts"), list):
        return result["artifacts"]
    return []


def _quality(thread_id: str, workspace: str) -> RunSnapshotQuality:
    quality = _safe_call({"status": "unknown", "checks": []}, lambda: build_quality_gate(thread_id, workspace))
    checks = quality.get("checks") if isinstance(quality, dict) and isinstance(quality.get("checks"), list) else []
    risks: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("status") in {"failed", "warning"}:
            risks.append(
                {
                    "id": check.get("id", ""),
                    "severity": check.get("severity", "warning"),
                    "title": check.get("label", ""),
                    "detail": check.get("detail", ""),
                    "source": "quality_gate",
                }
            )
    return RunSnapshotQuality(
        status=str(quality.get("status") or "unknown") if isinstance(quality, dict) else "unknown",
        score=None,
        gates=checks,
        risks=risks,
    )


def _approvals(thread_id: str, workspace: str) -> list[dict[str, Any]]:
    approvals_dir = Path(workspace).resolve() / ".nanocursor" / "runs" / _safe_run_id(thread_id) / "approvals"
    if not approvals_dir.exists():
        return []
    approvals: list[dict[str, Any]] = []
    for path in sorted(approvals_dir.glob("*.json")):
        data = _read_json(path)
        if isinstance(data, dict) and data.get("status") == "pending":
            approvals.append(data)
    return approvals


def build_run_snapshot(thread_id: str, workspace_dir: str | None = None) -> RunSnapshot:
    """Build a read-only aggregate snapshot for one run."""
    workspace = _workspace(workspace_dir)
    workspace_str = str(workspace)
    store = get_event_store()
    session = store.get_session(thread_id, workspace_str)
    events = store.list_events(thread_id, workspace_str)
    active_ids = _active_thread_ids()
    status = str((session or {}).get("status") or ("running" if thread_id in active_ids else "missing"))
    outcome = _safe_call({}, lambda: build_run_outcome(thread_id, workspace_str))

    return RunSnapshot(
        run=RunSnapshotRun(
            thread_id=thread_id,
            status=status,
            mode=str((session or {}).get("mode") or "agenthub_delivery"),
            prompt=str((session or {}).get("prompt") or ""),
            created_at=(session or {}).get("created_at"),
            updated_at=(session or {}).get("updated_at"),
            strategy=_session_strategy(session),
            is_active=thread_id in active_ids or status in ACTIVE_STATUSES,
        ),
        workspace=_workspace_snapshot(workspace),
        conversation=_conversation(session, events),
        activity=_activity(events),
        agents=_agents(thread_id, workspace_str),
        tasks=_tasks(thread_id, workspace_str),
        approvals=_approvals(thread_id, workspace_str),
        changes=_changes(thread_id, workspace_str),
        artifacts=_artifacts(thread_id, workspace_str),
        quality=_quality(thread_id, workspace_str),
        timeline=_timeline(events),
        outcome=outcome if isinstance(outcome, dict) else {},
    )
