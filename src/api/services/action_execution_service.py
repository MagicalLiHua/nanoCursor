"""Action execution service — unified pipeline for all high-risk workspace actions.

R5 pipeline: request -> path guard -> policy check -> approval if needed -> execute -> audit -> event
"""

from __future__ import annotations

import time
import uuid
import shutil
from pathlib import Path
from typing import Any

from src.api.services.checkpoint_service import create_checkpoint
from src.api.services.event_store import get_event_store
from src.api.services.approval_service import get_tool_approval
from src.api.services.mcp_runtime_service import call_mcp_tool
from src.api.services.mcp_status_service import record_mcp_usage
from src.runtime.action_policy import (
    ActionDecision,
    ActionKind,
    ActionRequest,
    check_action,
)
from src.runtime.audit_log import AuditRecord, get_audit_repo
from src.runtime.command_runner import run_command
from src.infra import config as config_module
from src.infra.path_guard import resolve_workspace_path, safe_slug


def _now() -> float:
    return time.time()


def check_and_decide(
    kind: str,
    target: str = "",
    thread_id: str = "",
    workspace_dir: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pre-flight check: determine if an action is allowed and needs approval.

    This is the check-only endpoint — does not execute anything.
    """
    try:
        action_kind = ActionKind(kind)
    except ValueError:
        return {
            "allowed": False,
            "requires_approval": False,
            "reason": f"未知的 action kind: {kind}",
            "risk": "high",
        }

    effective_workspace = workspace_dir or config_module.WORKSPACE_DIR
    loop_guard = _check_loop_guard(thread_id, effective_workspace, kind=kind, target=target, payload=payload)
    if loop_guard:
        return loop_guard

    # Path guard for file/delete actions
    if action_kind in (ActionKind.WRITE_FILE, ActionKind.DELETE_FILE, ActionKind.READ_FILE):
        if target:
            try:
                resolve_workspace_path(effective_workspace, target)
            except ValueError as e:
                return {
                    "allowed": False,
                    "requires_approval": False,
                    "reason": f"路径检查失败: {e}",
                    "risk": "high",
                }

    decision = check_action(action_kind, target, thread_id, effective_workspace, payload=payload)
    return {
        "allowed": decision.allowed,
        "requires_approval": decision.requires_approval,
        "reason": decision.reason,
        "risk": decision.risk,
        "permission_level": decision.permission_level,
    }


def _check_loop_guard(
    thread_id: str,
    workspace_dir: str,
    *,
    kind: str,
    target: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not thread_id:
        return None
    try:
        from src.api.services.agent_loop_state_service import check_loop_action_guard

        return check_loop_action_guard(thread_id, workspace_dir, kind=kind, target=target, payload=payload)
    except Exception:
        return None


def execute_action(
    kind: str,
    target: str = "",
    payload: dict[str, Any] | None = None,
    thread_id: str = "",
    workspace_dir: str = "",
) -> dict[str, Any]:
    """Execute an action through the full pipeline: check -> approve -> audit.

    For operations that need approval, this returns a 'blocked' result
    with an approval_id. The caller should use the approval flow, then
    re-invoke with the approval_id.
    """
    payload = payload or {}
    effective_workspace = workspace_dir or config_module.WORKSPACE_DIR
    try:
        action_kind = ActionKind(kind)
    except ValueError:
        return _audit_and_return(
            thread_id=thread_id, kind=kind, target=target,
            allowed=False, decision="denied", result="failure",
            reason=f"未知 action kind: {kind}",
            workspace_dir=effective_workspace,
        )

    loop_guard = _check_loop_guard(thread_id, effective_workspace, kind=kind, target=target, payload=payload)
    if loop_guard:
        return _audit_and_return(
            thread_id=thread_id,
            kind=kind,
            target=target,
            allowed=False,
            decision="denied",
            result="failure",
            reason=loop_guard["reason"],
            risk=loop_guard.get("risk", "high"),
            workspace_dir=effective_workspace,
            detail={"permission_level": loop_guard.get("permission_level", "")},
        )

    # 1. Path guard
    if action_kind in (ActionKind.WRITE_FILE, ActionKind.DELETE_FILE, ActionKind.READ_FILE):
        if target:
            try:
                resolve_workspace_path(effective_workspace, target)
            except ValueError as e:
                return _audit_and_return(
                    thread_id=thread_id, kind=kind, target=target,
                    allowed=False, decision="denied", result="failure",
                    reason=f"路径检查失败: {e}",
                    risk="high",
                    workspace_dir=effective_workspace,
                )

    # 2. Policy check
    decision = check_action(action_kind, target, thread_id, effective_workspace, payload=payload)

    if not decision.allowed:
        return _audit_and_return(
            thread_id=thread_id, kind=kind, target=target,
            allowed=False, decision="denied", result="failure",
            reason=decision.reason, risk=decision.risk,
            workspace_dir=effective_workspace,
        )

    # 3. Approval gate
    if decision.requires_approval:
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            approval_id = _create_pending_action_approval(
                action_kind=action_kind,
                target=target,
                payload=payload,
                thread_id=thread_id,
                workspace_dir=effective_workspace,
                decision=decision,
            )
            return _audit_and_return(
                thread_id=thread_id, kind=kind, target=target,
                allowed=True, decision="approved", result="pending",
                reason=f"需要审批: {decision.reason}", risk=decision.risk,
                approval_id=approval_id,
                workspace_dir=effective_workspace,
                detail={"permission_level": decision.permission_level},
            )

        approval = get_tool_approval(thread_id, approval_id, effective_workspace)
        approval_error = _validate_action_approval(approval, action_kind, target)
        if approval_error:
            return _audit_and_return(
                thread_id=thread_id, kind=kind, target=target,
                allowed=False, decision="denied", result="failure",
                reason=approval_error, risk=decision.risk,
                approval_id=approval_id,
                workspace_dir=effective_workspace,
            )

        started = time.monotonic()
        try:
            execution = _execute_approved_action(
                action_kind=action_kind,
                target=target,
                payload=payload,
                thread_id=thread_id,
                workspace_dir=effective_workspace,
            )
        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            return _audit_and_return(
                thread_id=thread_id, kind=kind, target=target,
                allowed=True, decision="approved", result="failure",
                reason=str(exc), risk=decision.risk,
                approval_id=approval_id,
                workspace_dir=effective_workspace,
                duration_ms=duration_ms,
                detail={"error": str(exc), "permission_level": decision.permission_level},
            )

        duration_ms = round((time.monotonic() - started) * 1000)
        return _audit_and_return(
            thread_id=thread_id, kind=kind, target=target,
            allowed=True, decision="approved", result=execution["result"],
            reason=execution["reason"], risk=decision.risk,
            approval_id=approval_id,
            workspace_dir=effective_workspace,
            duration_ms=duration_ms,
            detail={**execution.get("detail", {}), "permission_level": decision.permission_level},
        )

    # 4. Execute low/medium risk actions.
    started = time.monotonic()
    try:
        execution = _execute_low_risk_action(
            action_kind=action_kind,
            target=target,
            payload=payload,
            thread_id=thread_id,
            workspace_dir=effective_workspace,
        )
    except Exception as exc:
        duration_ms = round((time.monotonic() - started) * 1000)
        return _audit_and_return(
            thread_id=thread_id, kind=kind, target=target,
            allowed=True, decision="auto_allowed", result="failure",
            reason=str(exc), risk=decision.risk,
            workspace_dir=effective_workspace,
            duration_ms=duration_ms,
            detail={"error": str(exc), "permission_level": decision.permission_level},
        )

    duration_ms = round((time.monotonic() - started) * 1000)
    return _audit_and_return(
        thread_id=thread_id, kind=kind, target=target,
            allowed=True, decision="auto_allowed", result=execution["result"],
            reason=execution["reason"], risk=decision.risk,
            workspace_dir=effective_workspace,
            duration_ms=duration_ms,
            detail={**execution.get("detail", {}), "permission_level": decision.permission_level},
        )


def _execute_low_risk_action(
    action_kind: ActionKind,
    target: str,
    payload: dict[str, Any],
    thread_id: str,
    workspace_dir: str,
) -> dict[str, Any]:
    """Execute actions that policy allows without approval."""
    if action_kind == ActionKind.READ_FILE:
        path = resolve_workspace_path(workspace_dir, target, must_exist=True)
        max_chars = int(payload.get("max_chars") or payload.get("limit") or 100_000)
        content = path.read_text(encoding=payload.get("encoding") or "utf-8", errors="replace")
        rel_path = path.relative_to(resolve_workspace_path(workspace_dir, ".")).as_posix()
        return {
            "result": "success",
            "reason": f"已读取文件: {rel_path}",
            "detail": {
                "path": rel_path,
                "content": content[:max(max_chars, 0)],
                "truncated": len(content) > max_chars,
                "size": path.stat().st_size,
            },
        }

    if action_kind == ActionKind.WRITE_FILE:
        path = resolve_workspace_path(workspace_dir, target)
        workspace = resolve_workspace_path(workspace_dir, ".")
        rel_path = path.relative_to(workspace).as_posix()
        checkpoint: dict[str, Any] | None = None
        if path.exists() and path.is_file():
            checkpoint = create_checkpoint(
                rel_path,
                reason="action execute write_file",
                thread_id=thread_id,
                workspace_dir=workspace_dir,
            )

        content = str(payload.get("content", ""))
        encoding = str(payload.get("encoding") or "utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return {
            "result": "success",
            "reason": f"已写入文件: {rel_path}",
            "detail": {
                "path": rel_path,
                "bytes_written": len(content.encode(encoding, errors="replace")),
                "checkpoint": checkpoint,
            },
        }

    if action_kind == ActionKind.RUN_COMMAND:
        return _run_command_action(target, payload, workspace_dir)

    if action_kind == ActionKind.MCP_CALL:
        return _run_mcp_call_action(target, payload, thread_id, workspace_dir)

    return {
        "result": "success",
        "reason": f"已记录动作: {action_kind.value}",
        "detail": {"payload": payload},
    }


def _create_pending_action_approval(
    action_kind: ActionKind,
    target: str,
    payload: dict[str, Any],
    thread_id: str,
    workspace_dir: str,
    decision: ActionDecision,
) -> str:
    approval_id = f"approval_{uuid.uuid4().hex[:12]}"
    from src.api.services.approval_service import create_tool_approval
    create_tool_approval(
        thread_id=thread_id,
        decision={
            "decision_id": approval_id,
            "tool": target or action_kind.value,
            "kind": action_kind.value,
            "target": target,
            "payload": {key: value for key, value in payload.items() if key != "approval_id"},
            "status": "pending",
            "requires_approval": True,
            "allowed": True,
            "reason": decision.reason,
            "risk_level": decision.risk,
        },
        workspace_dir=workspace_dir,
    )
    return approval_id


def _validate_action_approval(
    approval: dict[str, Any] | None,
    action_kind: ActionKind,
    target: str,
) -> str:
    if approval is None:
        return "审批记录不存在。"
    if approval.get("status") != "approved":
        return f"审批状态不是 approved: {approval.get('status')}"
    if approval.get("kind") and approval.get("kind") != action_kind.value:
        return "审批记录与当前动作类型不匹配。"
    if approval.get("target") and approval.get("target") != target:
        return "审批记录与当前动作目标不匹配。"
    return ""


def _execute_approved_action(
    action_kind: ActionKind,
    target: str,
    payload: dict[str, Any],
    thread_id: str,
    workspace_dir: str,
) -> dict[str, Any]:
    """Execute high-risk actions after approval has been verified."""
    if action_kind == ActionKind.RUN_COMMAND:
        return _run_command_action(target, payload, workspace_dir)

    if action_kind == ActionKind.DELETE_FILE:
        path = resolve_workspace_path(workspace_dir, target, must_exist=True)
        if not path.is_file():
            raise ValueError("delete_file 当前只支持删除普通文件。")

        workspace = resolve_workspace_path(workspace_dir, ".")
        rel_path = path.relative_to(workspace).as_posix()
        size = path.stat().st_size
        checkpoint = create_checkpoint(
            rel_path,
            reason="action execute delete_file",
            thread_id=thread_id,
            workspace_dir=workspace_dir,
        )

        trash_root = _trash_root(workspace, thread_id)
        trash_name = f"{int(time.time() * 1000)}_{safe_slug(rel_path)}"
        trash_path = trash_root / trash_name
        if trash_path.exists():
            trash_path = trash_root / f"{trash_name}_{uuid.uuid4().hex[:8]}"

        shutil.move(str(path), str(trash_path))
        return {
            "result": "success",
            "reason": f"已移动到回收区: {rel_path}",
            "detail": {
                "path": rel_path,
                "bytes_moved": size,
                "checkpoint": checkpoint,
                "trash_path": str(trash_path),
                "trash_relative_path": trash_path.relative_to(workspace).as_posix(),
            },
        }

    if action_kind == ActionKind.MCP_CALL:
        return _run_mcp_call_action(target, payload, thread_id, workspace_dir)

    raise ValueError(f"{action_kind.value} 审批后执行尚未接入。")


def _run_mcp_call_action(
    target: str,
    payload: dict[str, Any],
    thread_id: str,
    workspace_dir: str,
) -> dict[str, Any]:
    server_id, tool_name = _parse_mcp_call_target(target, payload)
    if not server_id or not tool_name:
        raise ValueError("mcp_call 需要 server_id 和 tool_name。")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    timeout_seconds = int(payload.get("timeout_seconds") or 10)
    result = call_mcp_tool(
        server_id=server_id,
        tool_name=tool_name,
        arguments=arguments,
        workspace_dir=workspace_dir,
        timeout_seconds=timeout_seconds,
    )
    if result.get("ok"):
        record_mcp_usage(server_id, thread_id, workspace_dir)
    return {
        "result": "success" if result.get("ok") else "failure",
        "reason": "MCP 工具调用成功。" if result.get("ok") else f"MCP 工具调用失败: {result.get('error', 'unknown')}",
        "detail": result,
    }


def _run_command_action(target: str, payload: dict[str, Any], workspace_dir: str) -> dict[str, Any]:
    if not target.strip():
        raise ValueError("run_command 需要 command target。")
    timeout_seconds = int(payload.get("timeout_seconds") or 120)
    timeout_seconds = max(1, min(timeout_seconds, 600))
    result = run_command(
        target,
        cwd=resolve_workspace_path(workspace_dir, "."),
        timeout_seconds=timeout_seconds,
        max_stdout_chars=int(payload.get("max_stdout_chars") or 100_000),
        max_stderr_chars=int(payload.get("max_stderr_chars") or 20_000),
    )
    ok = result.get("exit_code") == 0 and not result.get("timed_out")
    return {
        "result": "success" if ok else "failure",
        "reason": "命令执行成功。" if ok else "命令执行失败。",
        "detail": result,
    }


def _trash_root(workspace: Path, thread_id: str) -> Path:
    root = workspace / ".nanocursor" / "trash" / safe_slug(thread_id or "workspace")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _parse_mcp_call_target(target: str, payload: dict[str, Any]) -> tuple[str, str]:
    server_id = str(payload.get("server_id") or payload.get("server") or "").strip()
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()

    raw_target = str(target or "").strip()
    if raw_target and (not server_id or not tool_name):
        for separator in ("::", "/", ":"):
            if separator in raw_target:
                left, right = raw_target.split(separator, 1)
                server_id = server_id or left.strip()
                tool_name = tool_name or right.strip()
                break
        else:
            tool_name = tool_name or raw_target

    if server_id and not server_id.startswith("mcp."):
        server_id = f"mcp.{server_id}"
    return server_id, tool_name


def record_action_result(
    thread_id: str,
    action_id: str,
    result: str,
    detail: dict[str, Any] | None = None,
    duration_ms: int = 0,
    workspace_dir: str | None = None,
) -> None:
    """Record the outcome of a previously approved/executed action."""
    repo = get_audit_repo()
    record = AuditRecord(
        audit_id=f"audit_{uuid.uuid4().hex[:12]}",
        thread_id=thread_id,
        action_id=action_id,
        result=result,
        duration_ms=duration_ms,
        detail=detail or {},
        created_at=time.time(),
    )
    repo.append(record, workspace_dir)


def get_audit_trail(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Return the audit trail for a run."""
    repo = get_audit_repo()
    records = repo.list(thread_id, workspace_dir)
    count = repo.count(thread_id, workspace_dir)
    return {
        "thread_id": thread_id,
        "records": [r.model_dump() for r in records],
        "total": count,
    }


def _audit_and_return(
    thread_id: str,
    kind: str,
    target: str,
    allowed: bool,
    decision: str,
    result: str,
    reason: str,
    risk: str = "medium",
    approval_id: str | None = None,
    workspace_dir: str | None = None,
    duration_ms: int = 0,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write audit record and return standardized response."""
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    repo = get_audit_repo()
    audit_detail = dict(detail or {})
    audit_detail["risk"] = risk
    if approval_id:
        audit_detail["approval_id"] = approval_id

    record = AuditRecord(
        audit_id=f"audit_{uuid.uuid4().hex[:12]}",
        thread_id=thread_id,
        action_id=action_id,
        kind=kind,
        target=target,
        decision=decision,
        result=result,
        reason=reason,
        duration_ms=duration_ms,
        detail=audit_detail,
        created_at=time.time(),
    )
    repo.append(record, workspace_dir)
    _emit_action_event(record, workspace_dir)
    return {
        "action_id": action_id,
        "thread_id": thread_id,
        "allowed": allowed,
        "requires_approval": decision == "approved" and result == "pending",
        "reason": reason,
        "risk": risk,
        "permission_level": audit_detail.get("permission_level", ""),
        "approval_id": approval_id,
        "result": result,
        "detail": audit_detail,
    }


def _emit_action_event(record: AuditRecord, workspace_dir: str | None = None) -> None:
    try:
        get_event_store().append_event(
            thread_id=record.thread_id,
            event_type="action_executed" if record.result == "success" else "action_failed",
            title=f"Action {record.kind}: {record.result}",
            content=record.reason,
            agent="system",
            payload={
                "action_id": record.action_id,
                "kind": record.kind,
                "target": record.target,
                "decision": record.decision,
                "result": record.result,
                "duration_ms": record.duration_ms,
                "detail": record.detail,
            },
            workspace_dir=workspace_dir,
        )
    except Exception:
        pass
