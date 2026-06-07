"""Shape recovery and retry evidence for ContextPack construction."""

from __future__ import annotations

import json
from typing import Any


def load_run_retry_context(thread_id: str | None, workspace_dir: str) -> dict[str, Any]:
    """Load structured retry evidence persisted on a retry run session."""
    if not thread_id:
        return {}
    try:
        from src.api.services.event_store import get_event_store

        session = get_event_store().get_session(thread_id, workspace_dir) or {}
    except Exception:
        return {}
    retry_context = session.get("retry_context")
    if not isinstance(retry_context, dict):
        return {}
    return {
        "original_thread_id": str(session.get("original_thread_id") or "")[:160],
        "retry_mode": str(session.get("retry_mode") or "")[:80],
        "failed_stage_id": str(retry_context.get("failed_stage_id") or "")[:160],
        "failed_stage": retry_context.get("failed_stage")
        if isinstance(retry_context.get("failed_stage"), dict) else {},
        "failure": retry_context.get("failure")
        if isinstance(retry_context.get("failure"), dict) else {},
        "recent_errors": retry_context.get("recent_errors")
        if isinstance(retry_context.get("recent_errors"), list) else [],
    }


def build_retry_failure_context(
    retry_context: dict[str, Any],
    index_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert a retry run's original failure evidence into ContextPack failures."""
    if not retry_context:
        return []
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    known_paths = {str(path) for path in entries}
    failure = retry_context.get("failure") if isinstance(retry_context.get("failure"), dict) else {}
    failed_stage = retry_context.get("failed_stage") if isinstance(retry_context.get("failed_stage"), dict) else {}
    recent_errors = retry_context.get("recent_errors") if isinstance(retry_context.get("recent_errors"), list) else []
    evidence = failure.get("evidence") if isinstance(failure.get("evidence"), dict) else {}
    text_blob = _jsonish_text(retry_context)
    recorded_files = failure.get("related_files") if isinstance(failure.get("related_files"), list) else []
    related_files = _unique([
        *[str(path) for path in recorded_files if path],
        *[path for path in known_paths if path and path in text_blob],
    ])[:12]
    related_tasks = _unique([
        str(value)
        for value in (
            failed_stage.get("id"),
            retry_context.get("failed_stage_id"),
            evidence.get("task_id"),
            evidence.get("stage_id"),
        )
        if value
    ])
    source_events = _unique([
        str(value)
        for value in (
            evidence.get("event_id"),
            *(
                event.get("payload", {}).get("event_id")
                for event in recent_errors
                if isinstance(event, dict) and isinstance(event.get("payload"), dict)
            ),
        )
        if value
    ])
    if not failure and not failed_stage and not recent_errors:
        return []
    return [{
        "id": str(failure.get("failure_id") or f"retry-{retry_context.get('original_thread_id') or 'failure'}"),
        "category": str(failure.get("failure_class") or evidence.get("failure_category") or "unknown"),
        "severity": "high",
        "summary": str(
            failure.get("title")
            or failed_stage.get("failure")
            or failed_stage.get("title")
            or "Retrying a previous run failure"
        )[:160],
        "detail": str(
            evidence.get("error_detail")
            or evidence.get("event_content")
            or failed_stage.get("failure")
            or (recent_errors[-1].get("content") if recent_errors and isinstance(recent_errors[-1], dict) else "")
        )[:500],
        "related_files": related_files,
        "related_tasks": related_tasks,
        "source_events": source_events,
        "confidence": 0.95 if failure.get("failure_class") else 0.7,
        "can_auto_retry": bool(failure.get("can_auto_retry")),
        "recovery_actions": [
            str(action.get("action_id") or action.get("label") or "")
            for action in failure.get("suggested_actions", [])[:8]
            if isinstance(action, dict) and (action.get("action_id") or action.get("label"))
        ] if isinstance(failure.get("suggested_actions"), list) else [],
        "source": "retry_context",
        "original_thread_id": retry_context.get("original_thread_id"),
    }]


def merge_failure_context_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge failure sources without losing the stronger retry evidence."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "") or _jsonish_text(item)[:240]
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    result.sort(
        key=lambda item: (
            item.get("source") != "retry_context",
            item.get("severity") != "high",
            -float(item.get("confidence") or 0),
        )
    )
    return result[:12]


def build_compact_recovery_context(
    recovery: dict[str, Any],
    failures: list[dict[str, Any]],
    *,
    retry_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one compact recovery instruction block for the current run."""
    retry_context = retry_context if isinstance(retry_context, dict) else {}
    summary = recovery.get("summary") if isinstance(recovery.get("summary"), dict) else {}
    actions = recovery.get("actions") if isinstance(recovery.get("actions"), list) else []
    failure_groups = recovery.get("failure_groups") if isinstance(recovery.get("failure_groups"), list) else []
    related_files = _unique([
        str(path)
        for failure in failures
        if isinstance(failure, dict)
        for path in failure.get("related_files", [])
        if path
    ])[:20]
    compact_actions = []
    for action in [*_retry_recovery_actions(retry_context), *actions][:10]:
        if not isinstance(action, dict):
            continue
        compact_actions.append({
            "id": str(action.get("id") or "")[:120],
            "priority": str(action.get("priority") or "low")[:40],
            "risk_level": str(action.get("risk_level") or "safe")[:40],
            "title": str(action.get("title") or "")[:240],
            "detail": str(action.get("detail") or "")[:500],
            "action_type": str(action.get("action_type") or "")[:100],
            "target": str(action.get("target") or "")[:240],
            "enabled": bool(action.get("enabled")),
        })
    compact_groups = []
    for group in failure_groups[:8]:
        if not isinstance(group, dict):
            continue
        compact_groups.append({
            "category": str(group.get("category") or "unknown")[:100],
            "count": int(group.get("count") or 0),
            "summary": str(group.get("summary") or "")[:300],
            "risk_ids": [str(item)[:120] for item in group.get("risk_ids", [])[:8]]
            if isinstance(group.get("risk_ids"), list) else [],
        })
    if not failures:
        return {}
    status = str(recovery.get("status") or "unknown")
    if retry_context and status in {"safe", "unprotected", "unknown"}:
        status = "attention"
    high_failure_count = sum(
        1 for failure in failures
        if isinstance(failure, dict) and failure.get("severity") == "high"
    )
    return {
        "status": status,
        "original_thread_id": retry_context.get("original_thread_id"),
        "retry_mode": retry_context.get("retry_mode"),
        "failed_stage_id": retry_context.get("failed_stage_id"),
        "risk_count": int(summary.get("risk_count") or len(failures)),
        "high_risk_count": int(summary.get("high_risk_count") or high_failure_count),
        "has_recovery_points": bool(summary.get("has_recovery_points")),
        "related_files": related_files,
        "failure_groups": compact_groups,
        "actions": compact_actions,
    }


def _retry_recovery_actions(retry_context: dict[str, Any]) -> list[dict[str, Any]]:
    failure = retry_context.get("failure") if isinstance(retry_context.get("failure"), dict) else {}
    actions = failure.get("suggested_actions") if isinstance(failure.get("suggested_actions"), list) else []
    result = []
    for action in actions[:8]:
        if not isinstance(action, dict):
            continue
        result.append({
            "id": str(action.get("action_id") or action.get("label") or "")[:120],
            "priority": "high",
            "risk_level": "guarded" if action.get("mode") in {"confirm", "manual"} else "safe",
            "title": str(action.get("label") or "Retry recovery action")[:240],
            "detail": str(action.get("description") or "")[:500],
            "action_type": str(action.get("mode") or "manual")[:100],
            "target": str(retry_context.get("failed_stage_id") or "")[:240],
            "enabled": True,
        })
    return result


def _jsonish_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
