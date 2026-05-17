"""Delivery report generation for AgentHub runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _status_text(session: dict[str, Any] | None) -> str:
    if not session:
        return "unknown"
    return session.get("status") or "unknown"


def _stage_lines(session: dict[str, Any] | None) -> list[str]:
    if not session:
        return ["- No execution plan recorded."]
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    if not stages:
        return ["- No execution stages recorded."]

    lines = ["| Stage | Owner | Status | Evidence |", "|---|---|---|---|"]
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        evidence = stage.get("tool_evidence") if isinstance(stage.get("tool_evidence"), list) else []
        tools = ", ".join(str(item.get("tool")) for item in evidence[-4:] if isinstance(item, dict) and item.get("tool"))
        failure = stage.get("failure") or ""
        evidence_text = tools or failure or "-"
        lines.append(
            f"| {stage.get('title') or stage.get('id') or '-'} "
            f"| {stage.get('owner') or '-'} "
            f"| {stage.get('status') or 'pending'} "
            f"| {evidence_text} |"
        )
    return lines


def build_delivery_report(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read or generate a delivery report for the run."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(thread_id, workspace_dir)
    report_path = run_dir / "report.md"

    if report_path.exists():
        markdown = report_path.read_text(encoding="utf-8", errors="replace")
        return {
            "thread_id": thread_id,
            "workspace_dir": str(workspace),
            "summary": "Loaded saved delivery report.",
            "markdown": markdown,
            "source": "run_artifact",
        }

    store = get_event_store()
    session = store.get_session(thread_id, str(workspace))
    events = store.list_events(thread_id, str(workspace))
    diff_info = get_run_diff(thread_id, str(workspace))
    changed_files = diff_info.get("changed_files", [])

    prompt = session.get("prompt", "") if session else ""
    assistant_messages = [event.content for event in events if event.type == "assistant_message"]
    tool_events = [event for event in events if event.type == "tool_call_finished"]
    error_events = [event for event in events if event.type == "error"]

    summary = (
        assistant_messages[-1][:300]
        if assistant_messages
        else "AgentHub run has not produced a final assistant message yet."
    )
    status = _status_text(session)

    lines = [
        "# AgentHub Delivery Report",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Request",
        "",
        prompt or "(no prompt recorded)",
        "",
        "## Run Status",
        "",
        f"- Thread: `{thread_id}`",
        f"- Status: `{status}`",
        f"- Workspace: `{workspace}`",
        "",
        "## Execution Stages",
        "",
        *_stage_lines(session),
        "",
        "## Changed Files",
        "",
    ]

    if changed_files:
        lines.extend(f"- {item.get('path', '')} ({item.get('change_type', 'modified')})" for item in changed_files)
    else:
        lines.append("- No changed files detected yet.")

    lines.extend(["", "## Tool Calls", ""])
    if tool_events:
        for event in tool_events[-10:]:
            tool = event.payload.get("tool") if isinstance(event.payload, dict) else ""
            lines.append(f"- {tool or event.title}")
    else:
        lines.append("- No tool calls recorded yet.")

    lines.extend(["", "## Risks", ""])
    if error_events:
        lines.extend(f"- {event.content}" for event in error_events[-5:])
    else:
        lines.append("- No blocking runtime errors recorded.")

    lines.extend(
        [
            "",
            "## Next Steps",
            "",
            "- Connect task/team events to structured Planner, Coder, Reviewer and Tester stages.",
            "- Add preview and test artifacts after the implementation command finishes.",
        ]
    )

    markdown = "\n".join(lines)
    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "summary": summary,
        "markdown": markdown,
        "requirements": [prompt] if prompt else [],
        "changed_files": changed_files,
        "risks": [event.content for event in error_events[-5:]],
        "source": "generated",
    }
