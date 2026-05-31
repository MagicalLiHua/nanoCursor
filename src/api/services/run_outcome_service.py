"""Unified run outcome aggregation for nanoCursor.

The outcome object is the frontend-facing summary of a run. It keeps the old
specialized services intact, but gives the UI one stable shape for the main
workbench state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.api.services.artifact_service import build_artifact_center
from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.api.services.quality_service import build_quality_gate
from src.api.services.recovery_service import build_recovery_center
from src.api.services.report_service import build_delivery_report
from src.api.services.traceability_service import build_requirement_traceability
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _safe_call(default: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = fn()
    except Exception as exc:  # outcome should degrade, not take down the run page
        return {**default, "error": str(exc)}
    return result if isinstance(result, dict) else default


def _execution_plan(session: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(session, dict):
        return {}
    plan = session.get("execution_plan")
    return plan if isinstance(plan, dict) else {}


def _events_summary(events: list[Any]) -> dict[str, Any]:
    last = events[-1] if events else None
    return {
        "count": len(events),
        "last_type": getattr(last, "type", "") if last else "",
        "last_title": getattr(last, "title", "") if last else "",
    }


def _final_message(events: list[Any]) -> str:
    for event in reversed(events):
        if getattr(event, "type", "") == "assistant_message":
            return str(getattr(event, "content", "") or "")
    return ""


def _change_stats(changed_files: list[dict[str, Any]]) -> dict[str, int]:
    stats = {
        "created": 0,
        "modified": 0,
        "deleted": 0,
        "renamed": 0,
        "total": len(changed_files),
    }
    for item in changed_files:
        change_type = str(item.get("change_type") or item.get("status") or "modified").lower()
        if change_type in {"added", "add", "created", "??", "a"}:
            stats["created"] += 1
        elif change_type in {"deleted", "delete", "removed", "d"}:
            stats["deleted"] += 1
        elif change_type in {"renamed", "rename", "r"}:
            stats["renamed"] += 1
        else:
            stats["modified"] += 1
    return stats


def _conversation_team_source(session: dict[str, Any] | None, workspace: str) -> str:
    conversation_id = str((session or {}).get("conversation_id") or "")
    if not conversation_id:
        return ""
    try:
        from src.api.services.conversation_service import get_conversation

        conversation = get_conversation(conversation_id, workspace)
        team = conversation.get("team", {}) if isinstance(conversation, dict) else {}
        return str(team.get("source") or "")
    except Exception:
        return ""


def _error_events(events: list[Any]) -> list[Any]:
    return [event for event in events if getattr(event, "type", "") == "error"]


def _risk_level(
    *,
    session: dict[str, Any] | None,
    strategy: str,
    quality: dict[str, Any],
    recovery: dict[str, Any],
    events: list[Any],
) -> str:
    write_like = strategy not in {"lead_direct_reply", "analysis_only"}
    if (session or {}).get("status") == "failed" or _error_events(events):
        return "high"

    recovery_risks = recovery.get("risks") if isinstance(recovery.get("risks"), list) else []
    high_non_quality_recovery = any(
        risk.get("severity") == "high" and not str(risk.get("id") or "").startswith("quality-")
        for risk in recovery_risks
        if isinstance(risk, dict)
    )
    high_recovery = int(recovery.get("summary", {}).get("high_risk_count") or 0)
    if high_non_quality_recovery or (write_like and high_recovery):
        return "high"

    if write_like and int(quality.get("failed_count") or 0) > 0:
        return "high"

    if not write_like:
        return "low"

    if int(quality.get("warning_count") or 0) > 0 or int(recovery.get("summary", {}).get("risk_count") or 0) > 0:
        return "medium"
    return "low"


def _report_payload(report: dict[str, Any]) -> dict[str, Any]:
    source = str(report.get("source") or "")
    markdown = str(report.get("markdown") or "")
    return {
        "applicable": source != "not_applicable" and bool(markdown),
        "source": source,
        "summary": report.get("summary", ""),
        "markdown": markdown,
        "reason": source if source == "not_applicable" else "",
        "changed_files": report.get("changed_files", []),
        "risks": report.get("risks", []),
        "agent_contributions": report.get("agent_contributions", {}),
    }


def build_run_outcome(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Build the stable frontend-facing outcome object for a run."""
    workspace = _workspace(workspace_dir)
    workspace_str = str(workspace)
    store = get_event_store()
    session = store.get_session(thread_id, workspace_str)
    events = store.list_events(thread_id, workspace_str)
    plan = _execution_plan(session)
    strategy = str(plan.get("strategy") or "")

    diff = _safe_call(
        {"thread_id": thread_id, "workspace_dir": workspace_str, "diff": "", "changed_files": [], "source": "missing"},
        lambda: get_run_diff(thread_id, workspace_str),
    )
    changed_files = diff.get("changed_files") if isinstance(diff.get("changed_files"), list) else []

    report = _safe_call(
        {"thread_id": thread_id, "workspace_dir": workspace_str, "summary": "", "markdown": "", "source": "missing"},
        lambda: build_delivery_report(thread_id, workspace_str),
    )
    quality = _safe_call(
        {"thread_id": thread_id, "workspace_dir": workspace_str, "status": "unknown", "checks": []},
        lambda: build_quality_gate(thread_id, workspace_str),
    )
    traceability = _safe_call(
        {"thread_id": thread_id, "workspace_dir": workspace_str, "requirements": [], "coverage_rate": 0},
        lambda: build_requirement_traceability(thread_id, workspace_str),
    )
    recovery = _safe_call(
        {"thread_id": thread_id, "workspace_dir": workspace_str, "status": "unknown", "summary": {}, "actions": []},
        lambda: build_recovery_center(thread_id, workspace_str),
    )
    artifacts = _safe_call(
        {"thread_id": thread_id, "workspace_dir": workspace_str, "status": "missing", "artifacts": [], "summary": {}},
        lambda: build_artifact_center(thread_id, workspace_str),
    )

    final_message = _final_message(events)
    report_info = _report_payload(report)
    risk_level = _risk_level(
        session=session,
        strategy=strategy,
        quality=quality,
        recovery=recovery,
        events=events,
    )

    return {
        "thread_id": thread_id,
        "workspace_dir": workspace_str,
        "status": (session or {}).get("status", "missing"),
        "mode": (session or {}).get("mode", "agenthub_delivery"),
        "prompt": (session or {}).get("prompt", ""),
        "conversation_id": (session or {}).get("conversation_id"),
        "created_at": (session or {}).get("created_at"),
        "updated_at": (session or {}).get("updated_at"),
        "strategy": strategy,
        "summary": {
            "title": (session or {}).get("prompt", "")[:80],
            "final_message": final_message,
            "has_code_changes": bool(changed_files),
            "has_report": report_info["applicable"],
            "risk_level": risk_level,
            "quality_status": quality.get("status", "unknown"),
        },
        "team": {
            "persistent_source": _conversation_team_source(session, workspace_str),
            "runtime_source": (session or {}).get("runtime_team_source", ""),
            "members": (session or {}).get("team", []),
        },
        "stages": plan.get("stages", []) if isinstance(plan.get("stages"), list) else [],
        "tasks": plan.get("tasks", []) if isinstance(plan.get("tasks"), list) else [],
        "changes": {
            "files": changed_files,
            "diff": diff.get("diff", ""),
            "source": diff.get("source", "unknown"),
            "error": diff.get("error", ""),
            "stats": _change_stats(changed_files),
        },
        "quality": quality,
        "traceability": traceability,
        "report": report_info,
        "recovery": {
            "available": bool(recovery.get("actions") or recovery.get("recovery_points")),
            **recovery,
        },
        "artifacts": artifacts,
        "events": _events_summary(events),
    }
