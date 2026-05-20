"""Run ledger service — record tool calls and steps during agent loop execution.

All writes go through the RunLedgerRepository. This service provides the
convenience layer that the agent loop's on_tool_call and lifecycle hooks call.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from src.runtime.run_ledger import (
    RunLedger,
    RunLedgerRepository,
    StepRecord,
    ToolCallRecord,
    get_ledger_repo,
)


def _repo() -> RunLedgerRepository:
    return get_ledger_repo()


def _now() -> float:
    return time.time()


# ---- Tool calls ----


def record_tool_call_start(
    thread_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    step_id: str = "",
    approval_id: str = "",
    workspace_dir: str | None = None,
) -> ToolCallRecord:
    record = ToolCallRecord(
        call_id=f"call_{uuid.uuid4().hex[:12]}",
        thread_id=thread_id,
        step_id=step_id,
        tool_name=tool_name,
        input_json=str(tool_input),
        status="started",
        started_at=_now(),
        approval_id=approval_id,
    )
    _repo().append_tool_call(thread_id, record, workspace_dir)
    return record


def record_tool_call_finish(
    call_id: str,
    thread_id: str,
    output: str = "",
    ok: bool = True,
    workspace_dir: str | None = None,
) -> None:
    """Update a tool call record with output and completion status.

    Since tools.jsonl is append-only, we write a new record with the same
    call_id and updated fields. Readers deduplicate by call_id, taking the
    latest entry.
    """
    record = ToolCallRecord(
        call_id=call_id,
        thread_id=thread_id,
        tool_name="",
        output_tail=(output or "")[:5000],
        status="completed" if ok else "failed",
        started_at=0.0,
        completed_at=_now(),
    )
    _repo().append_tool_call(thread_id, record, workspace_dir)


# ---- Steps ----


def record_steps(
    thread_id: str,
    stages: list[dict[str, Any]],
    workspace_dir: str | None = None,
) -> None:
    """Persist the current lifecycle stages as step records."""
    steps: list[StepRecord] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        steps.append(StepRecord(
            step_id=str(stage.get("id", stage.get("stage_id", ""))),
            thread_id=thread_id,
            title=str(stage.get("title", stage.get("id", ""))),
            owner=str(stage.get("owner", "")),
            status=str(stage.get("status", "pending")),
            started_at=float(stage.get("started_at", 0.0)),
            completed_at=float(stage.get("completed_at", 0.0)),
            error=str(stage.get("failure", "")),
        ))
    if steps:
        _repo().write_steps(thread_id, steps, workspace_dir)


def sync_steps_from_lifecycle(
    thread_id: str,
    metadata: dict[str, Any],
    workspace_dir: str | None = None,
) -> None:
    """Extract stages from RunContext metadata.lifecycle and persist."""
    lifecycle = metadata.get("lifecycle", {}) if metadata else {}
    stages = lifecycle.get("stages", []) if isinstance(lifecycle, dict) else []
    if stages:
        record_steps(thread_id, stages, workspace_dir)


# ---- Ledger queries ----


def get_run_ledger(thread_id: str, workspace_dir: str | None = None) -> RunLedger | None:
    return _repo().build_ledger(thread_id, workspace_dir)


def get_run_steps(thread_id: str, workspace_dir: str | None = None) -> list[StepRecord]:
    return _repo().get_steps(thread_id, workspace_dir)


def get_run_tools(thread_id: str, workspace_dir: str | None = None) -> list[ToolCallRecord]:
    """Return deduplicated tool calls (latest entry per call_id)."""
    all_calls = _repo().get_tool_calls(thread_id, workspace_dir)
    seen: dict[str, ToolCallRecord] = {}
    for tc in all_calls:
        if tc.call_id in seen:
            # Merge: later entries carry completion info
            existing = seen[tc.call_id]
            if tc.status != "started":
                existing.status = tc.status
            if tc.output_tail:
                existing.output_tail = tc.output_tail
            if tc.completed_at:
                existing.completed_at = tc.completed_at
            if tc.tool_name:
                existing.tool_name = tc.tool_name
        else:
            seen[tc.call_id] = tc
    return sorted(seen.values(), key=lambda r: r.started_at)
