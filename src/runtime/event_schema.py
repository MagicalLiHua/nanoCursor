"""Typed event schema for nanoCursor run events.

New events carry schema_version and typed payloads so frontend, report, quality,
and recovery code can read event fields reliably.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


class RunEvent(BaseModel):
    """Canonical run event consumed by the frontend and derived services."""
    schema_version: str = SCHEMA_VERSION
    id: str
    thread_id: str
    type: str
    title: str = ""
    content: str = ""
    agent: str = "system"
    timestamp: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed payloads for core event types
# ---------------------------------------------------------------------------


class ToolCallPayload(BaseModel):
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    ok: bool = True
    capability_trace: dict[str, Any] = Field(default_factory=dict)
    stage_id: str = ""
    loop_step_id: str = ""
    loop_action_type: str = ""
    loop_recorded: bool = False
    loop_step_error: str = ""


class StageUpdatedPayload(BaseModel):
    stage_id: str
    status: str
    owner: str = ""
    progress: int = 0


class FileChangedPayload(BaseModel):
    path: str
    change_type: str = "modified"
    tool: str = ""


class ApprovalPayload(BaseModel):
    plan_id: str
    decision: str = ""
    required_by: str = ""


# ---------------------------------------------------------------------------
# Normalize: make old / loose events look like RunEvent
# ---------------------------------------------------------------------------


def normalize_event(raw: dict[str, Any]) -> RunEvent:
    """Convert a dict (old or new) into a validated RunEvent.

    - Events without ``schema_version`` default to ``"0.x"``.
    - Missing fields get sensible defaults.
    - ``type`` is read from ``event_type`` as a fallback.
    """
    data: dict[str, Any] = dict(raw)
    data.setdefault("schema_version", "0.x")
    data.setdefault("id", raw.get("id") or raw.get("event_id") or "")
    data.setdefault("type", raw.get("type") or raw.get("event_type") or "unknown")
    data.setdefault("thread_id", raw.get("thread_id") or "")
    data.setdefault("title", raw.get("title") or "")
    data.setdefault("content", raw.get("content") or "")
    data.setdefault("agent", raw.get("agent") or "system")
    data.setdefault("timestamp", raw.get("timestamp") or raw.get("created_at") or 0.0)
    data.setdefault("payload", raw.get("payload") or {})

    # Only keep fields that belong to RunEvent
    field_names = set(RunEvent.model_fields.keys())
    filtered = {k: v for k, v in data.items() if k in field_names}

    return RunEvent(**filtered)


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a payload dict against the typed model for *event_type*.

    Returns the validated payload dict (coerced by Pydantic) or the original
    dict if no specific model matches.
    """
    model_map: dict[str, type[BaseModel]] = {
        "tool_call_finished": ToolCallPayload,
        "stage_updated": StageUpdatedPayload,
        "file_changed": FileChangedPayload,
        "approval_resolved": ApprovalPayload,
        "plan_approved": ApprovalPayload,
    }
    model = model_map.get(event_type)
    if model is None:
        return payload
    try:
        return model(**payload).model_dump()
    except Exception:
        return payload
