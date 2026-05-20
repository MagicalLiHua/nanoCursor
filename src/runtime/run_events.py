"""Unified event schema and enrichment utilities."""

from __future__ import annotations

import time
import uuid
from typing import Any

SCHEMA_VERSION = 1

SEVERITY_MAP: dict[str, str] = {
    "error": "error",
    "recovery_action_failed": "error",
    "dangerous_command": "warning",
    "recovery_action_started": "info",
    "recovery_action_completed": "info",
    "done": "info",
    "approval_requested": "warning",
    "approval_resolved": "info",
}


def detect_severity(event_type: str, payload: dict[str, Any] | None = None) -> str:
    """Infer event severity from type and payload."""
    if event_type in SEVERITY_MAP:
        return SEVERITY_MAP[event_type]

    if event_type == "tool_call_finished":
        trace = (payload or {}).get("capability_trace", {})
        if isinstance(trace, dict) and not trace.get("ok", True):
            return "error"
        return "info"

    if event_type in ("stage_updated", "task_created", "task_updated", "file_changed",
                      "diff_updated", "plan_created", "team_updated", "workspace_opened"):
        return "info"

    if event_type in ("orchestration_applied", "blueprint_generated"):
        return "info"

    return "info"


def enrich_event(
    event_dict: dict[str, Any],
    *,
    thread_id: str = "",
    conversation_id: str = "",
    workspace_id: str = "",
    stage_id: str = "",
    agent: str = "lead",
    **extra: Any,
) -> dict[str, Any]:
    """Add standard fields to an event dict. Does not mutate the original."""
    enriched = dict(event_dict)
    enriched.setdefault("event_id", str(uuid.uuid4()))
    enriched.setdefault("schema_version", SCHEMA_VERSION)
    enriched.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    if "severity" not in enriched:
        enriched["severity"] = detect_severity(
            enriched.get("type", ""), enriched.get("payload")
        )
    if thread_id:
        enriched.setdefault("thread_id", thread_id)
    if conversation_id:
        enriched.setdefault("conversation_id", conversation_id)
    if workspace_id:
        enriched.setdefault("workspace_id", workspace_id)
    if stage_id:
        enriched.setdefault("stage_id", stage_id)
    if agent:
        enriched.setdefault("agent", agent)
    enriched.update(extra)
    return enriched
