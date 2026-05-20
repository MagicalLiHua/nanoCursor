"""Observability: aggregate run events into stage/tool/file/policy metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.api.services.event_store import get_event_store
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def build_run_observability(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Aggregate session, events, stages, tools, files, policy, recovery into one view."""
    store = get_event_store()
    session = store.get_session(thread_id, str(_workspace(workspace_dir)))
    events = store.list_events(thread_id, str(_workspace(workspace_dir)))

    if not session and not events:
        return {"thread_id": thread_id, "status": "not_found"}

    plan = session.get("execution_plan", {}) if session else {}

    # ---- Timeline ----
    timeline = _build_timeline(events)

    # ---- Stage metrics ----
    stage_metrics = _build_stage_metrics(plan, events)

    # ---- Tool metrics ----
    tool_metrics = _build_tool_metrics(events)

    # ---- File changes ----
    file_changes = _build_file_changes(events)

    # ---- Policy ----
    policy_events = [e for e in events if e.type in {
        "tool_policy_checked", "tool_policy_blocked", "tool_budget_exceeded",
        "tool_approval_required",
    }]
    policy_checks = [
        {"type": e.type, "payload": e.payload, "time": e.timestamp}
        for e in policy_events[-20:]
    ]

    # ---- Recovery ----
    risk_count = sum(1 for e in events if e.type == "error")

    return {
        "thread_id": thread_id,
        "status": session.get("status", "unknown") if session else "unknown",
        "strategy": plan.get("strategy", "unknown"),
        "started_at": session.get("created_at") if session else None,
        "timeline": timeline,
        "stage_metrics": stage_metrics,
        "tool_metrics": tool_metrics,
        "file_changes": file_changes,
        "policy": {
            "checks": policy_checks,
            "violations": len([p for p in policy_checks if p["type"] == "tool_policy_blocked"]),
        },
        "recovery": {
            "risk_count": risk_count,
            "has_failures": risk_count > 0,
        },
    }


def _build_timeline(events: list[Any]) -> list[dict[str, Any]]:
    """Build a chronological event timeline (last 30 entries)."""
    timeline: list[dict[str, Any]] = []
    for event in events[-30:]:
        timeline.append({
            "type": event.type,
            "title": event.title or event.type,
            "agent": event.agent,
            "timestamp": event.timestamp if hasattr(event, "timestamp") else None,
        })
    return timeline


def _build_stage_metrics(plan: dict[str, Any] | None, events: list[Any]) -> list[dict[str, Any]]:
    """Compute per-stage metrics: duration, tool calls, file changes, errors."""
    stages = (plan or {}).get("stages", []) or []
    if not stages:
        return []

    metrics: list[dict[str, Any]] = []
    stage_updates = [e for e in events if e.type == "stage_updated"]

    for stage in stages:
        sid = stage.get("id", "")
        updates = [
            e for e in stage_updates
            if isinstance(e.payload, dict) and e.payload.get("stage_id") == sid
        ]
        tool_events_in_stage = [
            e for e in events if e.type == "tool_call_finished"
            and isinstance(e.payload, dict) and e.payload.get("stage_id") == sid
        ]
        file_events_in_stage = [
            e for e in events if e.type == "file_changed"
            and isinstance(e.payload, dict) and e.payload.get("stage_id") == sid
        ]
        errors_in_stage = [
            e for e in events if e.type == "error"
            and isinstance(e.payload, dict) and e.payload.get("stage_id") == sid
        ]

        duration_ms = None
        if stage.get("started_at") and stage.get("completed_at"):
            duration_ms = int((stage["completed_at"] - stage["started_at"]) * 1000)

        metrics.append({
            "stage_id": sid,
            "title": stage.get("title", sid),
            "status": stage.get("status", "pending"),
            "owner": stage.get("owner", ""),
            "duration_ms": duration_ms,
            "tool_calls": len(tool_events_in_stage),
            "file_changes": len(file_events_in_stage),
            "errors": len(errors_in_stage),
        })

    return metrics


def _build_tool_metrics(events: list[Any]) -> dict[str, Any]:
    """Aggregate tool call statistics."""
    tool_events = [e for e in events if e.type == "tool_call_finished"]
    by_tool: dict[str, int] = {}
    failed = 0
    for e in tool_events:
        tool = e.payload.get("tool", "unknown") if isinstance(e.payload, dict) else "unknown"
        by_tool[tool] = by_tool.get(tool, 0) + 1
        if isinstance(e.payload, dict) and not e.payload.get("ok", True):
            failed += 1

    return {
        "total": len(tool_events),
        "failed": failed,
        "success_rate": round(1 - failed / max(len(tool_events), 1), 2),
        "by_tool": by_tool,
    }


def _build_file_changes(events: list[Any]) -> list[dict[str, Any]]:
    """Extract file change events."""
    file_events = [e for e in events if e.type == "file_changed"]
    seen: set[str] = set()
    changes: list[dict[str, Any]] = []
    for e in file_events:
        path = e.payload.get("path", "") if isinstance(e.payload, dict) else ""
        if path and path not in seen:
            seen.add(path)
            changes.append({"path": path, "timestamp": e.timestamp if hasattr(e, "timestamp") else None})
    return changes


def build_workspace_observability(workspace_dir: str | None = None) -> dict[str, Any]:
    """Aggregate observability across recent runs in a workspace."""
    workspace = _workspace(workspace_dir)
    runs_dir = workspace / ".nanocursor" / "runs"
    if not runs_dir.exists():
        return {"runs": [], "trend": {}}

    run_summaries: list[dict[str, Any]] = []
    total_tools = 0
    total_errors = 0
    completed = 0

    for run_dir in sorted(runs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)[:20]:
        if not run_dir.is_dir():
            continue
        session_file = run_dir / "session.json"
        if not session_file.exists():
            continue
        try:
            session = json.loads(session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        thread_id = session.get("thread_id", run_dir.name)
        events_file = run_dir / "events.jsonl"
        tool_count = 0
        error_count = 0
        if events_file.exists():
            try:
                for line in events_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("type") == "tool_call_finished":
                        tool_count += 1
                    elif event.get("type") == "error":
                        error_count += 1
            except (json.JSONDecodeError, OSError):
                pass

        if session.get("status") == "completed":
            completed += 1
        total_tools += tool_count
        total_errors += error_count

        run_summaries.append({
            "thread_id": thread_id,
            "status": session.get("status", "unknown"),
            "prompt": (session.get("prompt", "") or "")[:100],
            "tool_calls": tool_count,
            "errors": error_count,
            "created_at": session.get("created_at"),
        })

    return {
        "runs": run_summaries,
        "trend": {
            "total_runs": len(run_summaries),
            "completed": completed,
            "avg_tool_calls": round(total_tools / max(len(run_summaries), 1), 1),
            "avg_errors": round(total_errors / max(len(run_summaries), 1), 1),
        },
    }
