"""Delivery report generation for nanoCursor runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.api.services.capability_usage_service import build_capability_usage
from src.api.services.diff_service import get_run_diff
from src.api.services.eval_service import build_aggregate_metrics
from src.api.services.event_store import get_event_store
from src.api.services.ephemeral_agent_service import summarize_ephemeral_agent_contributions
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


def _execution_strategy(session: dict[str, Any] | None) -> str:
    if not session:
        return ""
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    return str(plan.get("strategy") or "")


def _plain_assistant_summary(content: str) -> str:
    """Convert a final assistant message into a compact plain summary."""
    lines: list[str] = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.lstrip("#").strip()
        line = line.lstrip("-*").strip()
        line = line.replace("`", "")
        if line and line.lower() not in {"summary", "final", "最终交付报告", "delivery report"}:
            lines.append(line)
        if len(" ".join(lines)) >= 220:
            break
    return " ".join(lines)[:300]


def _generated_summary(
    *,
    prompt: str,
    assistant_messages: list[str],
    changed_files: list[dict[str, Any]],
    status: str,
) -> str:
    if changed_files:
        return f"本次运行完成 {len(changed_files)} 个文件变更，状态为 {status}。"
    if assistant_messages:
        summary = _plain_assistant_summary(assistant_messages[-1])
        if summary:
            return summary
    if prompt:
        return f"本次运行已处理请求：{prompt[:180]}"
    return "nanoCursor run has not produced a final assistant message yet."


def _report_not_applicable(
    *,
    session: dict[str, Any] | None,
    changed_files: list[dict[str, Any]],
    tool_events: list[Any],
    agent_contributions: dict[str, Any],
) -> bool:
    if _execution_strategy(session) != "lead_direct_reply":
        return False
    return not changed_files and not tool_events and not agent_contributions.get("contributions")


def build_delivery_report(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read or generate a delivery report for the run."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(thread_id, workspace_dir)
    report_path = run_dir / "report.md"

    if report_path.exists():
        markdown = report_path.read_text(encoding="utf-8", errors="replace")
        agent_contributions = summarize_ephemeral_agent_contributions(thread_id, str(workspace))
        if (
            agent_contributions.get("contributions")
            and "## Temporary Agent Contributions" not in markdown
        ):
            lines = ["", "## Temporary Agent Contributions", ""]
            lines.append("| Agent | Role | Status | Summary | Evidence | Risks |")
            lines.append("|---|---|---|---|---|---|")
            for item in agent_contributions["contributions"]:
                lines.append(
                    f"| {item.get('name') or item.get('agent_id')} "
                    f"| {item.get('role') or '-'} "
                    f"| {item.get('terminal_status') or item.get('status') or '-'} "
                    f"| {str(item.get('summary') or '-')[:180]} "
                    f"| {item.get('evidence_count', 0)} "
                    f"| {item.get('risk_count', 0)} |"
                )
            markdown = f"{markdown.rstrip()}\n" + "\n".join(lines) + "\n"
        return {
            "thread_id": thread_id,
            "workspace_dir": str(workspace),
            "summary": "Loaded saved delivery report.",
            "markdown": markdown,
            "agent_contributions": agent_contributions,
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
    agent_contributions = summarize_ephemeral_agent_contributions(thread_id, str(workspace))

    status = _status_text(session)
    if _report_not_applicable(
        session=session,
        changed_files=changed_files,
        tool_events=tool_events,
        agent_contributions=agent_contributions,
    ):
        return {
            "thread_id": thread_id,
            "workspace_dir": str(workspace),
            "summary": "轻量对话未涉及代码交付，因此未生成交付报告。",
            "markdown": "",
            "requirements": [prompt] if prompt else [],
            "changed_files": [],
            "risks": [],
            "capabilities_used": [],
            "agent_contributions": agent_contributions,
            "source": "not_applicable",
        }

    summary = _generated_summary(
        prompt=prompt,
        assistant_messages=assistant_messages,
        changed_files=changed_files,
        status=status,
    )

    lines = [
        "# nanoCursor Delivery Report",
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

    lines.extend(["", "## Temporary Agent Contributions", ""])
    contributions = agent_contributions.get("contributions", [])
    pending_agents = agent_contributions.get("pending_agents", [])
    if contributions:
        lines.append("| Agent | Role | Status | Summary | Evidence | Risks |")
        lines.append("|---|---|---|---|---|---|")
        for item in contributions:
            lines.append(
                f"| {item.get('name') or item.get('agent_id')} "
                f"| {item.get('role') or '-'} "
                f"| {item.get('terminal_status') or item.get('status') or '-'} "
                f"| {str(item.get('summary') or '-')[:180]} "
                f"| {item.get('evidence_count', 0)} "
                f"| {item.get('risk_count', 0)} |"
            )
    else:
        lines.append("- No temporary sub-agent contributions recorded.")
    if pending_agents:
        names = ", ".join(item.get("name") or item.get("agent_id") for item in pending_agents)
        lines.append(f"- Pending temporary agents: {names}")

    lines.extend(["", "## Tool Calls", ""])
    if tool_events:
        for event in tool_events[-10:]:
            tool = event.payload.get("tool") if isinstance(event.payload, dict) else ""
            lines.append(f"- {tool or event.title}")
    else:
        lines.append("- No tool calls recorded yet.")

    # Capabilities Used
    lines.extend(["", "## Capabilities Used", ""])
    cap_evidence: list[dict[str, Any]] = []
    try:
        cap_usage = build_capability_usage(thread_id, str(workspace))
        caps = cap_usage.get("capabilities", [])
        if caps:
            lines.append("| Capability | Kind | Status | Evidence |")
            lines.append("|---|---|---|---|")
            for cap in caps:
                ev_count = len(cap.get("evidence", []))
                lines.append(f"| {cap['name']} | {cap['kind']} | {cap['status']} | {ev_count} events |")
        else:
            lines.append("- No capability evidence recorded.")
        cap_evidence = caps
    except Exception:
        lines.append("- Capability usage data is not available for this run.")

    # Run Metrics
    lines.extend(["", "## Run Metrics", ""])
    lines.append(f"- LLM calls: {sum(1 for e in events if e.type == 'llm_response') or 'N/A'}")
    lines.append(f"- Tool calls: {len(tool_events)}")
    lines.append(f"- Errors: {len(error_events)}")
    lines.append(f"- Events total: {len(events)}")
    lines.append("")

    lines.extend(["", "## Risks", ""])
    if error_events:
        lines.extend(f"- {event.content}" for event in error_events[-5:])
    if agent_contributions.get("risks"):
        lines.extend(
            f"- [{risk.get('agent_name') or risk.get('agent_id')}] {risk.get('description') or risk}"
            for risk in agent_contributions["risks"][-5:]
        )
    if not error_events and not agent_contributions.get("risks"):
        lines.append("- No blocking runtime errors recorded.")

    agent_next_actions = agent_contributions.get("next_actions", [])
    lines.extend(
        [
            "",
            "## Next Steps",
            "",
            *[f"- {item}" for item in agent_next_actions[:5]],
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
        "risks": [event.content for event in error_events[-5:]] + agent_contributions.get("risks", []),
        "capabilities_used": cap_evidence,
        "agent_contributions": agent_contributions,
        "source": "generated",
    }
