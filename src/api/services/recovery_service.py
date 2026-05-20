"""Safety and recovery aggregation for AgentHub runs."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from src.api.services.event_store import get_event_store
from src.api.services.failure_classifier_service import classify_failure
from src.api.services.quality_service import build_quality_gate
from src.infra import config as config_module


DANGEROUS_COMMAND_MARKERS = [
    "rm -rf",
    "sudo ",
    "chmod 777",
    "mkfs",
    "dd if=",
    ":(){",
    "> /dev/",
]


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_join(root: Path, rel_path: str) -> Path:
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("Path escapes workspace.")
    return target


def _safe_run_dir(thread_id: str, workspace: Path) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return workspace / ".nanocursor" / "runs" / safe_id


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _target_guess_from_backup(name: str) -> str | None:
    if ".bak." not in name:
        return None
    safe_name = name.split(".bak.", 1)[0]
    return safe_name.replace("_", "/")


def _snapshot_points(workspace: Path) -> list[dict[str, Any]]:
    snapshots_dir = workspace / ".snapshots"
    if not snapshots_dir.exists():
        return []

    points = []
    for path in sorted((item for item in snapshots_dir.iterdir() if item.is_dir()), reverse=True):
        metadata = _read_json(path / "metadata.json") if (path / "metadata.json").exists() else {}
        active_files = metadata.get("active_files", []) if isinstance(metadata, dict) else []
        points.append(
            {
                "id": path.name,
                "kind": "snapshot",
                "label": f"Snapshot {path.name}",
                "status": "available",
                "timestamp": metadata.get("timestamp") if isinstance(metadata, dict) else None,
                "path": str(path),
                "reason": metadata.get("reason", "") if isinstance(metadata, dict) else "",
                "detail": f"{len(active_files)} active files captured.",
            }
        )
    return points


def _backup_points(workspace: Path) -> list[dict[str, Any]]:
    backups_dir = workspace / ".backups"
    if not backups_dir.exists():
        return []

    points = []
    for path in sorted((item for item in backups_dir.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        points.append(
            {
                "id": path.name,
                "kind": "backup",
                "label": path.name,
                "status": "available",
                "timestamp": stat.st_mtime,
                "path": str(path),
                "target_path": _target_guess_from_backup(path.name),
                "size": stat.st_size,
                "reason": "file_backup",
                "detail": "File backup can be restored with an explicit target path.",
            }
        )
    return points


def _run_risks(thread_id: str | None, workspace: Path) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    if not thread_id:
        return risks

    store = get_event_store()
    session = store.get_session(thread_id, str(workspace))
    events = store.list_events(thread_id, str(workspace))
    plan = session.get("execution_plan") if isinstance(session, dict) and isinstance(session.get("execution_plan"), dict) else {}
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    for stage in stages:
        if not isinstance(stage, dict) or stage.get("status") != "failed":
            continue
        risks.append(
            {
                "id": f"stage-{stage.get('id') or 'failed'}",
                "severity": "high",
                "title": "Execution stage failed",
                "detail": stage.get("failure") or f"{stage.get('title') or stage.get('id')} failed.",
                "evidence": {
                    "stage_id": stage.get("id"),
                    "stage_title": stage.get("title"),
                    "owner": stage.get("owner"),
                    "tool_evidence": stage.get("tool_evidence", []),
                },
            }
        )

    error_events = [event for event in events if event.type == "error"]
    for index, event in enumerate(error_events, start=1):
        classification = classify_failure(event.content or "")
        risks.append(
            {
                "id": f"error-{index}",
                "severity": "high",
                "title": "Run recorded an error event",
                "detail": event.content,
                "evidence": {
                    "event_id": event.id,
                    "agent": event.agent,
                    "failure_category": classification["category"],
                    "failure_confidence": classification["confidence"],
                    "failure_summary": classification["summary"],
                },
            }
        )

    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        command = ""
        if payload.get("tool") in {"bash", "run_shell", "shell"}:
            tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
            command = str(tool_input.get("command") or payload.get("command") or "")
        if not command:
            continue
        lowered = command.lower()
        marker = next((item for item in DANGEROUS_COMMAND_MARKERS if item in lowered), "")
        if marker:
            risks.append(
                {
                    "id": f"dangerous-command-{event.id}",
                    "severity": "high",
                    "title": "Potentially dangerous command observed",
                    "detail": command,
                    "evidence": {"marker": marker, "event_id": event.id},
                }
            )

    quality = build_quality_gate(thread_id, str(workspace))
    for check in quality.get("checks", []):
        if check.get("status") in {"failed", "warning"}:
            risks.append(
                {
                    "id": f"quality-{check.get('id')}",
                    "severity": "medium" if check.get("status") == "warning" else "high",
                    "title": check.get("label", "Quality check"),
                    "detail": check.get("detail", ""),
                    "evidence": check.get("evidence", {}),
                }
            )

    return risks


def _primary_recovery_point(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    preferred = next((point for point in points if point["kind"] == "snapshot"), None)
    return preferred or (points[0] if points else None)


def _action_risk_level(action_id: str) -> str:
    """Classify recovery action by risk level."""
    safe = {"inspect-failed-stage", "inspect-failure-event", "continue-delivery",
            "review-dangerous-command", "open-quality-gate", "create-recovery-point"}
    guarded = {"rerun-tests", "restore-last-safe-point"}
    destructive = {"restore-backup", "create-remediation-run"}
    if action_id in destructive:
        return "destructive"
    if action_id in guarded:
        return "guarded"
    return "safe"


def _recovery_actions(
    risks: list[dict[str, Any]],
    points: list[dict[str, Any]],
    thread_id: str | None,
) -> list[dict[str, Any]]:
    """Explain the next safe recovery steps for the current run."""
    actions: list[dict[str, Any]] = []
    recovery_point = _primary_recovery_point(points)
    has_failed_quality = any(risk["id"].startswith("quality-") and risk["severity"] == "high" for risk in risks)
    has_warning_quality = any(risk["id"].startswith("quality-") and risk["severity"] == "medium" for risk in risks)
    has_error = any(risk["id"].startswith("error-") for risk in risks)
    has_stage_failure = any(risk["id"].startswith("stage-") for risk in risks)
    has_dangerous_command = any(risk["id"].startswith("dangerous-command-") for risk in risks)

    if has_stage_failure:
        stage_risk = next((risk for risk in risks if risk["id"].startswith("stage-")), {})
        stage_id = stage_risk.get("evidence", {}).get("stage_id", "") if isinstance(stage_risk.get("evidence"), dict) else ""
        actions.append(
            {
                "id": "inspect-failed-stage",
                "priority": "high",
                "title": "查看失败阶段证据",
                "detail": "先查看失败阶段的负责人、工具证据和错误摘要，再决定重试、回滚或补测。",
                "action_type": "inspect_stage",
                "target": stage_id or thread_id or "",
                "enabled": bool(thread_id),
            }
        )

    if has_error:
        actions.append(
            {
                "id": "inspect-failure-event",
                "priority": "high",
                "title": "定位失败事件",
                "detail": "先查看时间线中的 error 事件，确认失败发生在哪个 Agent 和工具调用之后。",
                "action_type": "inspect_timeline",
                "target": thread_id or "",
                "enabled": bool(thread_id),
            }
        )

    if has_dangerous_command:
        actions.append(
            {
                "id": "review-dangerous-command",
                "priority": "high",
                "title": "复核高风险命令影响",
                "detail": "检测到潜在破坏性命令，建议先检查 Diff 和恢复点，再决定是否回滚。",
                "action_type": "review_diff",
                "target": thread_id or "",
                "enabled": bool(thread_id),
            }
        )

    if has_failed_quality or has_warning_quality:
        actions.append(
            {
                "id": "open-quality-gate",
                "priority": "high" if has_failed_quality else "medium",
                "title": "按质量门禁补齐缺失项",
                "detail": "根据失败或警告检查项补测试、补报告或修复运行错误，然后重新执行验证。",
                "action_type": "quality_gate",
                "target": thread_id or "",
                "enabled": bool(thread_id),
            }
        )

    if recovery_point and risks:
        actions.append(
            {
                "id": "restore-last-safe-point",
                "priority": "medium",
                "title": "保留最近恢复点作为回退方案",
                "detail": f"最近可用恢复点是 {recovery_point['label']}，如果修复失败可从这里恢复。",
                "action_type": "recovery_point",
                "target": recovery_point["id"],
                "enabled": True,
            }
        )
    elif risks:
        actions.append(
            {
                "id": "create-recovery-point",
                "priority": "medium",
                "title": "先创建恢复点再继续修复",
                "detail": "当前没有快照或备份。继续修改前建议先创建快照，避免扩大损失。",
                "action_type": "snapshot",
                "target": "",
                "enabled": False,
            }
        )

    if not actions:
        actions.append(
            {
                "id": "continue-delivery",
                "priority": "low",
                "title": "可以继续交付",
                "detail": "未发现阻塞风险。建议保持当前恢复点，并继续执行下一轮验证。",
                "action_type": "continue",
                "target": thread_id or "",
                "enabled": True,
            }
        )

    # Add risk_level to all actions
    for action in actions:
        action["risk_level"] = _action_risk_level(action["id"])

    return actions


def build_recovery_center(thread_id: str | None = None, workspace_dir: str | None = None) -> dict[str, Any]:
    """Build safety and recovery status from snapshots, backups, and run evidence."""
    workspace = _workspace(workspace_dir)
    points = _snapshot_points(workspace) + _backup_points(workspace)
    risks = _run_risks(thread_id, workspace)
    actions = _recovery_actions(risks, points, thread_id)

    status = "safe"
    if any(risk["severity"] == "high" for risk in risks):
        status = "attention"
    elif risks:
        status = "review"
    if not points:
        status = "unprotected" if status == "safe" else status

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "status": status,
        "generated_at": time.time(),
        "summary": {
            "snapshot_count": sum(1 for point in points if point["kind"] == "snapshot"),
            "backup_count": sum(1 for point in points if point["kind"] == "backup"),
            "risk_count": len(risks),
            "high_risk_count": sum(1 for risk in risks if risk["severity"] == "high"),
            "has_recovery_points": bool(points),
            "action_count": len(actions),
        },
        "recovery_points": points,
        "risks": risks,
        "actions": actions,
        "failure_groups": _build_failure_groups(risks),
    }


def _build_failure_groups(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group risks by failure category for structured display."""
    groups: dict[str, dict[str, Any]] = {}
    for risk in risks:
        evidence = risk.get("evidence", {}) if isinstance(risk.get("evidence"), dict) else {}
        category = evidence.get("failure_category", "unknown")
        if category not in groups:
            groups[category] = {
                "category": category,
                "count": 0,
                "risk_ids": [],
                "summary": evidence.get("failure_summary", ""),
            }
        groups[category]["count"] += 1
        groups[category]["risk_ids"].append(risk["id"])

    return sorted(groups.values(), key=lambda g: g["count"], reverse=True)


def rollback_from_backup(backup_name: str, target_path: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Restore a workspace file from a named backup file."""
    workspace = _workspace(workspace_dir)
    backups_dir = workspace / ".backups"
    backup_path = (backups_dir / backup_name).resolve()
    if not str(backup_path).startswith(str(backups_dir.resolve())):
        raise ValueError("Backup path escapes backup directory.")
    if not backup_path.exists() or not backup_path.is_file():
        raise FileNotFoundError(f"Backup not found: {backup_name}")

    target = _safe_join(workspace, target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, target)
    return {
        "restored": True,
        "backup_name": backup_name,
        "target_path": str(target.relative_to(workspace)),
        "message": f"Restored {target_path} from {backup_name}.",
    }
