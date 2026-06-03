"""Retry context collection and prompt construction.

This module keeps retry-specific evidence shaping out of the ASGI entrypoint.
It accepts the small runtime dependencies it needs from callers so it remains
easy to test and does not import the legacy ``api_server`` module.
"""

from __future__ import annotations

import json
from typing import Any

from src.api.services.failure_classifier_service import load_failures, save_failures


def collect_retry_context(
    *,
    thread_id: str,
    workspace_dir: str,
    event_store: Any,
    session: dict[str, Any] | None = None,
    failure_id: str | None = None,
) -> dict[str, Any]:
    """Collect compact failure and lifecycle evidence for a retry run."""
    session = session or {}
    lifecycle = session.get("lifecycle") if isinstance(session.get("lifecycle"), dict) else {}
    try:
        failures = load_failures(thread_id, workspace_dir)
        if not failures and session.get("status") == "failed":
            failures = save_failures(thread_id, workspace_dir)
    except Exception:
        failures = []

    selected_failure = None
    if failure_id:
        selected_failure = next((item for item in failures if item.failure_id == failure_id), None)
    if selected_failure is None and failures:
        selected_failure = failures[0]

    events = event_store.list_events(thread_id, workspace_dir)
    error_events = [event for event in events if event.type in {"error", "tool_policy_blocked", "test_finished"}]
    latest_errors = error_events[-3:]
    failed_stage_id = lifecycle.get("failed_stage_id")
    stages = session.get("execution_plan", {}).get("stages", []) if isinstance(session.get("execution_plan"), dict) else []
    failed_stage = next(
        (stage for stage in stages if isinstance(stage, dict) and stage.get("id") == failed_stage_id),
        None,
    )

    return {
        "status": session.get("status", ""),
        "failed_stage_id": failed_stage_id or "",
        "failed_stage": failed_stage or {},
        "failure": selected_failure.model_dump() if selected_failure else {},
        "recent_errors": [
            {
                "type": event.type,
                "title": event.title,
                "content": event.content[:800],
                "payload": event.payload,
            }
            for event in latest_errors
        ],
    }


def build_retry_prompt(
    *,
    original_prompt: str,
    original_thread_id: str,
    original_status: str,
    retry_mode: str,
    retry_context: dict[str, Any],
    instruction: str = "",
) -> str:
    """Build the user-facing prompt for a retry run."""
    failed_stage = retry_context.get("failed_stage") or {}
    failure = retry_context.get("failure") or {}
    recent_errors = retry_context.get("recent_errors") or []
    lines = [
        "这是一次 nanoCursor 重试运行，请基于原始需求和失败证据继续完成任务。",
        "",
        f"原始 Run: {original_thread_id}",
        f"原始状态: {original_status}",
        f"重试模式: {retry_mode}",
        "",
        "原始需求:",
        original_prompt or "(无原始需求)",
    ]
    if retry_mode == "failed_stage" and failed_stage:
        lines.extend([
            "",
            "优先重试失败阶段:",
            f"- 阶段: {failed_stage.get('title') or failed_stage.get('id')}",
            f"- 负责人: {failed_stage.get('owner') or 'Lead'}",
            f"- 失败原因: {failed_stage.get('failure') or retry_context.get('failed_stage_id') or '未知'}",
        ])
    if failure:
        evidence = failure.get("evidence") if isinstance(failure.get("evidence"), dict) else {}
        related_files = failure.get("related_files") if isinstance(failure.get("related_files"), list) else []
        lines.extend([
            "",
            "失败分类:",
            f"- 类型: {failure.get('failure_class') or 'unknown'}",
            f"- 标题: {failure.get('title') or '运行失败'}",
            f"- 关联文件: {', '.join(str(path) for path in related_files[:12]) if related_files else '未识别'}",
            f"- 证据: {json.dumps(evidence, ensure_ascii=False)[:1200]}",
        ])
    if recent_errors:
        lines.append("\n最近错误事件:")
        for event in recent_errors:
            lines.append(f"- [{event.get('type')}] {event.get('title')}: {event.get('content')}")
    if instruction.strip():
        lines.extend(["", "用户补充指令:", instruction.strip()])
    lines.extend([
        "",
        "执行要求:",
        "- 不要盲目重复上次失败路径，先复盘失败原因。",
        "- 只修改和本次需求相关的文件。",
        "- 如涉及代码修改，完成后给出验证命令和结果。",
        "- 最终回复说明本次重试相对原 run 的修复点、风险和下一步。",
    ])
    return "\n".join(lines)
