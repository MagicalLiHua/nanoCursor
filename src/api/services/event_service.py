"""Unified event emission service.

All new events flow through :func:`emit_event` so every event gets a
``schema_version``, a validated payload, and an ``event_id`` before being
written to the EventStore.

Old events read from disk are normalized through :func:`normalize_event`
so downstream services (observability, report, quality, recovery) always
see a consistent shape.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from src.api.services.event_store import EventStore, get_event_store
from src.runtime.event_schema import (
    RunEvent,
    SCHEMA_VERSION,
    normalize_event,
    validate_event_payload,
)


def emit_event(
    thread_id: str,
    event_type: str,
    *,
    title: str = "",
    content: str = "",
    agent: str = "system",
    payload: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
    event_store: EventStore | None = None,
) -> RunEvent:
    """Emit a new event with schema version, validated payload, and unique id.

    Always use this instead of calling ``event_store.append_event()`` directly.
    """
    store = event_store or get_event_store()
    payload = dict(payload or {})
    payload = validate_event_payload(event_type, payload)

    # Build via the existing AgentEvent path (backward-compat) then convert
    legacy = store.append_event(
        thread_id=thread_id,
        event_type=event_type,
        title=title,
        content=content,
        agent=agent,
        payload=payload,
        workspace_dir=workspace_dir,
    )

    return RunEvent(
        schema_version=SCHEMA_VERSION,
        id=legacy.id,
        thread_id=legacy.thread_id,
        type=legacy.type,
        title=legacy.title,
        content=legacy.content or "",
        agent=legacy.agent,
        timestamp=legacy.timestamp,
        payload=legacy.payload,
    )


def get_normalized_events(
    thread_id: str,
    *,
    workspace_dir: str | None = None,
    event_store: EventStore | None = None,
) -> list[RunEvent]:
    """Read all events for a run and return them as normalized RunEvents.

    Old events (without ``schema_version``) are upgraded with defaults.
    """
    store = event_store or get_event_store()
    raw_events = store.list_events(thread_id, workspace_dir)
    return [normalize_event(e.model_dump()) for e in raw_events]


def build_event_summary(events: list[RunEvent]) -> dict[str, Any]:
    """Build a lightweight summary from a list of normalized events.

    Useful for report headers and quick status checks without loading all payloads.
    """
    event_types: list[str] = []
    tool_calls = 0
    tool_failures = 0
    stages: set[str] = set()
    files_changed: set[str] = set()

    for ev in events:
        event_types.append(ev.type)
        if ev.type == "tool_call_finished":
            tool_calls += 1
            ok = ev.payload.get("ok", True)
            if not ok:
                tool_failures += 1
        if ev.type == "stage_updated":
            sid = ev.payload.get("stage_id", "")
            if sid:
                stages.add(sid)
        if ev.type == "file_changed":
            fp = ev.payload.get("path", "")
            if fp:
                files_changed.add(fp)

    return {
        "event_count": len(events),
        "schema_versions": sorted(set(e.schema_version for e in events)),
        "event_types": event_types,
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "stages_touched": sorted(stages),
        "files_changed_count": len(files_changed),
    }
