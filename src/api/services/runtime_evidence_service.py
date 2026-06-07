"""Collect delivery evidence for controller-owned runtime loops."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store


class RuntimeDeliveryEvidence(BaseModel):
    """Evidence used to decide whether a lightweight code task may finish."""

    thread_id: str
    changed_files: list[dict[str, Any]] = Field(default_factory=list)
    diff: str = ""
    diff_source: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    write_calls: list[dict[str, Any]] = Field(default_factory=list)
    check_calls: list[dict[str, Any]] = Field(default_factory=list)
    failed_calls: list[dict[str, Any]] = Field(default_factory=list)
    has_write_action: bool = False
    has_changes: bool = False
    has_verification: bool = False
    ready: bool = False
    reason: str = ""


def collect_runtime_delivery_evidence(
    thread_id: str,
    workspace_dir: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
) -> RuntimeDeliveryEvidence:
    """Collect Diff and tool evidence after a controller-owned code turn."""
    calls = [dict(item) for item in (tool_calls or []) if isinstance(item, dict)]
    diff_info = get_run_diff(thread_id, workspace_dir)
    changed_files = [
        dict(item)
        for item in diff_info.get("changed_files", [])
        if isinstance(item, dict)
    ]
    diff = str(diff_info.get("diff") or "")
    write_calls = [item for item in calls if str(item.get("tool") or "") in {"write_file", "edit_file"}]
    check_calls = [item for item in calls if _is_check_call(item)]
    failed_calls = [item for item in calls if not bool(item.get("ok", True))]
    failed_writes = [item for item in failed_calls if str(item.get("tool") or "") in {"write_file", "edit_file"}]
    successful_writes = [item for item in write_calls if bool(item.get("ok", True))]
    has_diff_event = any(
        event.type == "diff_updated"
        for event in get_event_store().list_events(thread_id, workspace_dir)
    )

    has_changes = bool(changed_files or diff.strip())
    has_verification = bool(check_calls or diff.strip() or has_diff_event)
    has_write_action = bool(successful_writes)
    ready = has_write_action and has_changes and has_verification and not failed_writes
    reason = _evidence_reason(
        ready=ready,
        has_write_action=has_write_action,
        has_changes=has_changes,
        has_verification=has_verification,
        failed_writes=failed_writes,
    )
    evidence = RuntimeDeliveryEvidence(
        thread_id=thread_id,
        changed_files=changed_files,
        diff=diff,
        diff_source=str(diff_info.get("source") or ""),
        tool_calls=calls,
        write_calls=write_calls,
        check_calls=check_calls,
        failed_calls=failed_calls,
        has_write_action=has_write_action,
        has_changes=has_changes,
        has_verification=has_verification,
        ready=ready,
        reason=reason,
    )
    get_event_store().append_event(
        thread_id,
        "runtime_delivery_evidence",
        title="运行交付证据已收集",
        content=reason,
        agent="lead",
        payload={
            "ready": ready,
            "reason": reason,
            "changed_files": changed_files,
            "changed_file_count": len(changed_files),
            "write_call_count": len(write_calls),
            "successful_write_call_count": len(successful_writes),
            "check_call_count": len(check_calls),
            "failed_call_count": len(failed_calls),
            "diff_source": evidence.diff_source,
            "has_diff_event": has_diff_event,
            "diff_preview": diff[:5000],
        },
        workspace_dir=workspace_dir,
    )
    return evidence


def _is_check_call(call: dict[str, Any]) -> bool:
    tool = str(call.get("tool") or "").lower()
    if tool in {"run_tests", "git_diff", "git_status"}:
        return True
    if tool != "bash":
        return False
    tool_input = call.get("input") if isinstance(call.get("input"), dict) else {}
    command = str(tool_input.get("command") or "").lower()
    return any(marker in command for marker in ("test", "pytest", "vitest", "jest", "lint", "ruff", "mypy"))


def _evidence_reason(
    *,
    ready: bool,
    has_write_action: bool,
    has_changes: bool,
    has_verification: bool,
    failed_writes: list[dict[str, Any]],
) -> str:
    if ready:
        return "已确认真实文件变更，并收集到 Diff 或检查证据。"
    if failed_writes:
        return "存在失败的写入工具调用，不能完成 small_edit。"
    if not has_write_action:
        return "未检测到本轮成功写入工具调用，不能完成 small_edit。"
    if not has_changes:
        return "未检测到真实文件变更，不能完成 small_edit。"
    if not has_verification:
        return "已检测到文件变更，但缺少 Diff 或检查证据。"
    return "small_edit 交付证据不足。"
