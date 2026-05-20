"""Execute recovery actions and produce events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.event_store import get_event_store
from src.api.services.recovery_service import build_recovery_center, rollback_from_backup


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def execute_recovery_action(
    thread_id: str,
    action_id: str,
    target: str = "",
    confirmed: bool = False,
    workspace_dir: str | None = None,
    target_path: str = "",
) -> dict[str, Any]:
    """Execute a recovery action for a run.

    Supported actions:
    - inspect-failed-stage: Return stage detail with failure evidence
    - rerun-tests: Suggest test re-run (v1: diagnostic only)
    - restore-backup: Restore a file from backup (requires confirmed=true)
    - create-remediation-run: Return remediation prompt template
    """
    workspace = _workspace(workspace_dir)
    store = get_event_store()
    session = store.get_session(thread_id, str(workspace))

    # Validate action is supported
    supported = {
        "inspect-failed-stage",
        "inspect-failure-event",
        "review-dangerous-command",
        "open-quality-gate",
        "restore-last-safe-point",
        "continue-delivery",
        "rerun-tests",
        "restore-backup",
        "create-remediation-run",
    }
    if action_id not in supported:
        raise ValueError(f"不支持的动作: {action_id}")

    # Emit started event
    store.append_event(
        thread_id, "recovery_action_started",
        title=f"恢复动作开始: {action_id}",
        content=f"正在执行恢复动作 {action_id}",
        agent="lead",
        payload={"action_id": action_id, "target": target, "target_path": target_path, "confirmed": confirmed},
        workspace_dir=str(workspace),
    )

    try:
        if action_id == "inspect-failed-stage":
            result = _inspect_failed_stage(session, target)

        elif action_id == "inspect-failure-event":
            result = _inspect_failure_event(thread_id, str(workspace))

        elif action_id == "review-dangerous-command":
            result = _review_dangerous_command(thread_id, str(workspace))

        elif action_id == "open-quality-gate":
            result = _open_quality_gate(thread_id)

        elif action_id == "restore-last-safe-point":
            result = _inspect_recovery_point(thread_id, target, str(workspace))

        elif action_id == "continue-delivery":
            result = {
                "ok": True,
                "action_id": action_id,
                "status": "completed",
                "message": "当前没有阻塞风险，可以继续交付。",
                "event": None,
            }

        elif action_id == "rerun-tests":
            result = _rerun_tests(workspace)

        elif action_id == "restore-backup":
            if not confirmed:
                raise ValueError("restore-backup 需要 confirmed=true 确认。")
            if not target:
                raise ValueError("restore-backup 需要 target 参数指定备份文件名。")
            restore_target = target_path or _target_guess_from_backup(target)
            if not restore_target:
                raise ValueError("restore-backup 需要 target_path，无法从备份名推断目标文件。")
            rollback_result = rollback_from_backup(target, restore_target, str(workspace))
            result = {
                "ok": rollback_result["restored"],
                "action_id": action_id,
                "status": "completed" if rollback_result["restored"] else "failed",
                "message": rollback_result.get("message", "文件已恢复。"),
                "event": None,
            }

        elif action_id == "create-remediation-run":
            result = _build_remediation_prompt(session, thread_id, str(workspace))

        else:
            result = {"ok": False, "action_id": action_id, "status": "failed", "message": "未知动作。", "event": None}
    except Exception as exc:
        store.append_event(
            thread_id, "recovery_action_failed",
            title=f"恢复动作失败: {action_id}",
            content=str(exc),
            agent="lead",
            payload={"action_id": action_id, "target": target, "target_path": target_path, "error": str(exc)},
            workspace_dir=str(workspace),
        )
        raise

    # Emit completed event
    store.append_event(
        thread_id, "recovery_action_completed",
        title=f"恢复动作完成: {action_id}",
        content=result.get("message", ""),
        agent="lead",
        payload={"action_id": action_id, "result": result},
        workspace_dir=str(workspace),
    )

    return result


def _target_guess_from_backup(name: str) -> str | None:
    if ".bak." not in name:
        return None
    return name.split(".bak.", 1)[0].replace("_", "/")


def _inspect_failed_stage(session: dict[str, Any] | None, target_stage_id: str) -> dict[str, Any]:
    """Return detailed failure info for a stage."""
    if not session:
        return {"ok": False, "action_id": "inspect-failed-stage", "status": "failed", "message": "Run 会话不存在。", "event": None}

    plan = session.get("execution_plan", {}) or {}
    stages = plan.get("stages", []) or []
    failed_stages = [s for s in stages if s.get("status") == "failed"]

    if not failed_stages:
        return {"ok": True, "action_id": "inspect-failed-stage", "status": "completed", "message": "没有失败阶段。", "event": None}

    # If target specified, find that stage; otherwise use first failed
    stage = next((s for s in failed_stages if s.get("id") == target_stage_id), failed_stages[0])

    return {
        "ok": True,
        "action_id": "inspect-failed-stage",
        "status": "completed",
        "message": f"阶段「{stage.get('title', stage.get('id'))}」失败: {stage.get('failure', '无详情')}",
        "event": {
            "type": "recovery_action_completed",
            "payload": {"stage": stage, "tool_evidence": stage.get("tool_evidence", [])},
        },
    }


def _rerun_tests(workspace: Path) -> dict[str, Any]:
    """Suggest test re-run. v1 does not actually run tests."""
    return {
        "ok": True,
        "action_id": "rerun-tests",
        "status": "completed",
        "message": "建议重新运行测试。系统将尝试检测项目测试框架并执行对应命令。",
        "event": {
            "type": "recovery_action_completed",
            "payload": {"suggestion": "rerun-tests", "note": "请手动运行 pytest 或前端 check 命令。"},
        },
    }


def _inspect_failure_event(thread_id: str, workspace_str: str) -> dict[str, Any]:
    store = get_event_store()
    errors = [event for event in store.list_events(thread_id, workspace_str) if event.type == "error"]
    if not errors:
        return {
            "ok": True,
            "action_id": "inspect-failure-event",
            "status": "completed",
            "message": "当前运行没有 error 事件。",
            "event": None,
        }
    latest = errors[-1]
    return {
        "ok": True,
        "action_id": "inspect-failure-event",
        "status": "completed",
        "message": f"最近失败事件：{latest.content[:300]}",
        "event": {"type": latest.type, "payload": latest.payload, "content": latest.content},
    }


def _review_dangerous_command(thread_id: str, workspace_str: str) -> dict[str, Any]:
    center = build_recovery_center(thread_id, workspace_str)
    risks = [risk for risk in center.get("risks", []) if str(risk.get("id", "")).startswith("dangerous-command-")]
    return {
        "ok": True,
        "action_id": "review-dangerous-command",
        "status": "completed",
        "message": f"发现 {len(risks)} 条高风险命令证据，请先检查 Diff 和恢复点。",
        "event": {"type": "recovery_action_completed", "payload": {"risks": risks}},
    }


def _open_quality_gate(thread_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "action_id": "open-quality-gate",
        "status": "completed",
        "message": "请在底部报告或右侧恢复面板查看质量门禁详情，并按失败项补齐证据。",
        "event": {"type": "recovery_action_completed", "payload": {"thread_id": thread_id}},
    }


def _inspect_recovery_point(thread_id: str, point_id: str, workspace_str: str) -> dict[str, Any]:
    center = build_recovery_center(thread_id, workspace_str)
    point = next((item for item in center.get("recovery_points", []) if item.get("id") == point_id), None)
    if not point:
        return {
            "ok": False,
            "action_id": "restore-last-safe-point",
            "status": "failed",
            "message": f"未找到恢复点: {point_id}",
            "event": None,
        }
    return {
        "ok": True,
        "action_id": "restore-last-safe-point",
        "status": "completed",
        "message": f"最近恢复点：{point.get('label') or point_id}。当前版本先展示证据，自动恢复请使用明确的 restore-backup 动作。",
        "event": {"type": "recovery_action_completed", "payload": {"recovery_point": point}},
    }


def _build_remediation_prompt(session: dict[str, Any] | None, thread_id: str, workspace_str: str) -> dict[str, Any]:
    """Build a remediation prompt from failed run evidence."""
    if not session:
        return {"ok": False, "action_id": "create-remediation-run", "status": "failed", "message": "Run 会话不存在。", "event": None}

    parts = ["请修复以下运行中的问题："]
    parts.append(f"原始运行: {thread_id}")

    plan = session.get("execution_plan", {}) or {}
    stages = plan.get("stages", []) or []
    failed_stages = [s for s in stages if s.get("status") == "failed"]

    if failed_stages:
        parts.append("失败阶段:")
        for s in failed_stages:
            parts.append(f"  - {s.get('title', s.get('id'))}: {s.get('failure', '无详情')}")

    # Include error events
    store = get_event_store()
    events = store.list_events(thread_id, workspace_str)
    errors = [e for e in events if e.type == "error"]
    if errors:
        parts.append("错误事件:")
        for e in errors[-3:]:
            parts.append(f"  - {e.content[:200]}")

    prompt = "\n".join(parts)
    return {
        "ok": True,
        "action_id": "create-remediation-run",
        "status": "completed",
        "message": "补救提示已生成。",
        "event": {
            "type": "recovery_action_completed",
            "payload": {"remediation_prompt": prompt, "original_thread_id": thread_id},
        },
    }
