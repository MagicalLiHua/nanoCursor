"""Artifact Center aggregation for nanoCursor runs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.api.models import AgentEvent
from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.api.services.parallel_agent_service import load_parallel_merge_plan, load_parallel_proposals
from src.api.services.quality_service import build_quality_gate
from src.api.services.report_service import build_delivery_report
from src.api.services.score_service import build_delivery_score
from src.api.services.traceability_service import build_requirement_traceability
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _payload(event: AgentEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _run_tasks(events: list[AgentEvent]) -> list[dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = _payload(event)
        if event.type == "task_created":
            task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            task_id = str(payload.get("task_id") or task.get("id") or event.id)
            tasks[task_id] = {
                "id": task_id,
                "title": task.get("title") or event.title,
                "status": task.get("status") or "pending",
                "owner": task.get("owner") or event.agent,
            }
        elif event.type == "task_updated":
            task_id = payload.get("task_id")
            if not task_id:
                continue
            current = tasks.setdefault(
                str(task_id),
                {"id": str(task_id), "title": event.title, "status": "pending", "owner": event.agent},
            )
            if payload.get("status"):
                current["status"] = payload["status"]
            if payload.get("title"):
                current["title"] = payload["title"]
            if payload.get("owner"):
                current["owner"] = payload["owner"]
    return list(tasks.values())


def _test_results(events: list[AgentEvent]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in events:
        if event.type != "test_finished":
            continue
        payload = _payload(event)
        results.append(
            {
                "title": event.title or "Verification finished",
                "status": payload.get("status") or "recorded",
                "checks": payload.get("checks") if isinstance(payload.get("checks"), list) else [],
                "content": event.content,
            }
        )
    return results


def _risks(quality: dict[str, Any], report: dict[str, Any], events: list[AgentEvent]) -> list[str]:
    risks: list[str] = []
    for check in quality.get("checks", []):
        if check.get("status") in {"failed", "warning"}:
            risks.append(f"{check.get('label')}: {check.get('detail')}")
    risks.extend(str(item) for item in report.get("risks", []) if item)
    risks.extend(event.content for event in events if event.type == "error" and event.content)
    return risks


def _status_for_artifact(required: bool, available: bool, warning: bool = False) -> str:
    if available and not warning:
        return "ready"
    if available and warning:
        return "warning"
    return "missing" if required else "empty"


def build_artifact_center(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Build a single delivery artifact index for the run."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(thread_id, str(workspace))
    store = get_event_store()
    session = store.get_session(thread_id, str(workspace))
    events = store.list_events(thread_id, str(workspace))
    diff = get_run_diff(thread_id, str(workspace))
    report = build_delivery_report(thread_id, str(workspace))
    quality = build_quality_gate(thread_id, str(workspace))
    score = build_delivery_score(thread_id, str(workspace))
    traceability = build_requirement_traceability(thread_id, str(workspace))
    parallel_proposals = load_parallel_proposals(thread_id, str(workspace))
    parallel_merge_plan = load_parallel_merge_plan(thread_id, str(workspace))

    tasks = _run_tasks(events)
    tests = _test_results(events)
    changed_files = diff.get("changed_files", [])
    risks = _risks(quality, report, events)
    report_path = run_dir / "report.md"
    diff_path = run_dir / "diff.patch"
    requirements_path = run_dir / "requirements.json"
    report_status = (
        "empty"
        if report.get("source") == "not_applicable"
        else _status_for_artifact(True, bool(report.get("markdown")))
    )

    artifacts = [
        {
            "id": "requirements",
            "kind": "requirements",
            "label": "需求摘要",
            "status": _status_for_artifact(True, traceability["total_count"] > 0, traceability["missing_count"] > 0),
            "summary": f"{traceability['covered_count']} / {traceability['total_count']} 个需求已覆盖",
            "path": str(requirements_path) if requirements_path.exists() else None,
            "count": traceability["total_count"],
            "payload": traceability,
        },
        {
            "id": "tasks",
            "kind": "tasks",
            "label": "任务清单",
            "status": _status_for_artifact(True, bool(tasks), any(task.get("status") != "completed" for task in tasks)),
            "summary": f"{sum(1 for task in tasks if task.get('status') == 'completed')} / {len(tasks)} 个任务已完成",
            "count": len(tasks),
            "payload": {"tasks": tasks},
        },
        {
            "id": "changed_files",
            "kind": "files",
            "label": "变更文件",
            "status": _status_for_artifact(True, bool(changed_files)),
            "summary": f"{len(changed_files)} 个文件发生变化",
            "path": str(run_dir / "changed_files.json") if (run_dir / "changed_files.json").exists() else None,
            "count": len(changed_files),
            "payload": {"changed_files": changed_files},
        },
        {
            "id": "diff_patch",
            "kind": "diff",
            "label": "Diff Patch",
            "status": _status_for_artifact(True, bool(diff.get("diff"))),
            "summary": f"Diff 来源：{diff.get('source', 'unknown')}",
            "path": str(diff_path) if diff_path.exists() else None,
            "count": len(str(diff.get("diff") or "").splitlines()),
            "payload": {"source": diff.get("source"), "diff": diff.get("diff", "")},
        },
        {
            "id": "tests",
            "kind": "tests",
            "label": "测试结果",
            "status": _status_for_artifact(False, bool(tests)),
            "summary": f"{len(tests)} 条验证记录",
            "count": len(tests),
            "payload": {"tests": tests},
        },
        {
            "id": "report",
            "kind": "report",
            "label": "交付报告",
            "status": report_status,
            "summary": report.get("summary", ""),
            "path": str(report_path) if report_path.exists() else None,
            "payload": report,
        },
        {
            "id": "quality",
            "kind": "quality",
            "label": "质量门禁",
            "status": "ready" if quality["status"] == "passed" else "warning" if quality["status"] == "warning" else "missing",
            "summary": f"{quality['passed_count']} passed, {quality['warning_count']} warning, {quality['failed_count']} failed",
            "count": len(quality.get("checks", [])),
            "payload": quality,
        },
        {
            "id": "score",
            "kind": "score",
            "label": "交付评分",
            "status": "ready" if score["level"] in {"excellent", "good"} else "warning",
            "summary": f"{score['score']} / 100 · {score['level']}",
            "count": score["score"],
            "payload": score,
        },
        {
            "id": "risks",
            "kind": "risks",
            "label": "风险清单",
            "status": "ready" if not risks else "warning",
            "summary": "未发现阻塞风险" if not risks else f"{len(risks)} 个风险或缺失项",
            "count": len(risks),
            "payload": {"risks": risks},
        },
        {
            "id": "parallel_proposals",
            "kind": "agent_proposals",
            "label": "并行 Agent 提案",
            "status": _status_for_artifact(False, bool(parallel_proposals.get("proposals"))),
            "summary": (
                f"{parallel_proposals.get('summary', {}).get('proposal_count', 0)} 个子 Agent 提案 · "
                f"{parallel_proposals.get('summary', {}).get('suggested_file_count', 0)} 个建议关注文件"
            ),
            "path": str(run_dir / "parallel_proposals.json") if (run_dir / "parallel_proposals.json").exists() else None,
            "count": parallel_proposals.get("summary", {}).get("proposal_count", 0),
            "payload": parallel_proposals,
        },
        {
            "id": "parallel_merge_plan",
            "kind": "agent_merge",
            "label": "Lead 合并策略",
            "status": _status_for_artifact(False, bool(parallel_merge_plan.get("accepted_proposals"))),
            "summary": (
                f"接受 {parallel_merge_plan.get('summary', {}).get('accepted_count', 0)} 个提案 · "
                f"暂缓 {parallel_merge_plan.get('summary', {}).get('deferred_count', 0)} 个"
            ),
            "path": str(run_dir / "parallel_merge_plan.json") if (run_dir / "parallel_merge_plan.json").exists() else None,
            "count": parallel_merge_plan.get("summary", {}).get("accepted_count", 0),
            "payload": parallel_merge_plan,
        },
    ]

    status = "ready"
    if any(item["status"] == "missing" for item in artifacts):
        status = "incomplete"
    elif any(item["status"] == "warning" for item in artifacts):
        status = "warning"

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "status": status,
        "generated_at": time.time(),
        "summary": {
            "run_status": session.get("status") if session else "unknown",
            "artifact_count": len(artifacts),
            "ready_count": sum(1 for item in artifacts if item["status"] == "ready"),
            "warning_count": sum(1 for item in artifacts if item["status"] == "warning"),
            "missing_count": sum(1 for item in artifacts if item["status"] == "missing"),
            "score": score["score"],
            "coverage_rate": traceability["coverage_rate"],
        },
        "artifacts": artifacts,
    }
