"""Aggregate capability usage evidence for a run."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.api.services.event_store import get_event_store


def build_capability_usage(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Return capability usage evidence for a run.

    Aggregates from session.json execution_plan (planned capabilities)
    and events.jsonl tool_call_finished (actual usage).
    """
    store = get_event_store()
    session = store.get_session(thread_id, workspace_dir)
    if not session:
        raise ValueError(f"Run 不存在: {thread_id}")

    events = store.list_events(thread_id, workspace_dir)

    # Collect planned capabilities from execution_plan.stages
    planned: dict[str, dict[str, Any]] = {}
    execution_plan = session.get("execution_plan", {}) or {}
    for stage in execution_plan.get("stages", []) or []:
        for cap_id in stage.get("capabilities", []) or []:
            if cap_id not in planned:
                planned[cap_id] = {
                    "id": cap_id,
                    "name": cap_id,
                    "kind": cap_id.split(".", 1)[0],
                    "status": "planned",
                    "evidence": [],
                }

    # Scan tool_call_finished events for actual usage
    used: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type != "tool_call_finished":
            continue
        trace = event.payload.get("capability_trace", {})
        if not isinstance(trace, dict):
            continue
        cap_id = trace.get("capability_id", "")
        if not cap_id:
            continue
        cap_name = trace.get("capability_name", cap_id)
        cap_kind = trace.get("kind", "tool")
        stage_id = event.payload.get("stage_id", "")

        if cap_id not in used:
            used[cap_id] = {
                "id": cap_id,
                "name": cap_name,
                "kind": cap_kind,
                "status": "used",
                "evidence": [],
            }
        used[cap_id]["evidence"].append({
            "event_type": "tool_call_finished",
            "tool": trace.get("tool", ""),
            "stage_id": stage_id,
        })

    # Merge: used overwrites planned for same capability
    capabilities: dict[str, dict[str, Any]] = {**planned, **used}

    # Also check orchestration_applied events for evidence
    for event in events:
        if event.type != "orchestration_applied":
            continue
        caps = event.payload.get("capabilities", []) or []
        for cap_id in caps:
            if cap_id in capabilities:
                capabilities[cap_id]["evidence"].append({
                    "event_type": "orchestration_applied",
                    "stage_id": event.payload.get("stage_id", ""),
                })

    cap_list = sorted(capabilities.values(), key=lambda c: (
        {"used": 0, "planned": 1}.get(c["status"], 2),
        c["kind"],
        c["name"],
    ))

    # Build summary
    used_count = sum(1 for c in cap_list if c["status"] == "used")
    skill_count = sum(1 for c in cap_list if c["kind"] == "skill")
    mcp_count = sum(1 for c in cap_list if c["kind"] == "mcp")
    tool_count = sum(1 for c in cap_list if c["kind"] == "tool")

    return {
        "thread_id": thread_id,
        "capabilities": cap_list,
        "summary": {
            "used_count": used_count,
            "planned_count": len(cap_list) - used_count,
            "skill_count": skill_count,
            "mcp_count": mcp_count,
            "tool_count": tool_count,
        },
    }
