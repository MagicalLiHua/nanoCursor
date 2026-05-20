"""Action execution service — unified pipeline for all high-risk workspace actions.

R5 pipeline: request -> path guard -> policy check -> approval if needed -> execute -> audit -> event
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from src.runtime.action_policy import (
    ActionDecision,
    ActionKind,
    ActionRequest,
    check_action,
)
from src.runtime.audit_log import AuditRecord, get_audit_repo
from src.infra import config as config_module
from src.infra.path_guard import resolve_workspace_path


def _now() -> float:
    return time.time()


def check_and_decide(
    kind: str,
    target: str = "",
    thread_id: str = "",
    workspace_dir: str = "",
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

    decision = check_action(action_kind, target, thread_id, effective_workspace)
    return {
        "allowed": decision.allowed,
        "requires_approval": decision.requires_approval,
        "reason": decision.reason,
        "risk": decision.risk,
    }


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
    decision = check_action(action_kind, target, thread_id, effective_workspace)

    if not decision.allowed:
        return _audit_and_return(
            thread_id=thread_id, kind=kind, target=target,
            allowed=False, decision="denied", result="failure",
            reason=decision.reason, risk=decision.risk,
            workspace_dir=effective_workspace,
        )

    # 3. Approval gate
    if decision.requires_approval:
        approval_id = f"approval_{uuid.uuid4().hex[:12]}"
        from src.api.services.approval_service import create_tool_approval
        create_tool_approval(
            thread_id=thread_id,
            decision={
                "decision_id": approval_id,
                "tool": target or kind,
                "status": "pending",
                "requires_approval": True,
                "allowed": True,
                "reason": decision.reason,
                "risk_level": decision.risk,
            },
            workspace_dir=effective_workspace,
        )
        return _audit_and_return(
            thread_id=thread_id, kind=kind, target=target,
            allowed=True, decision="approved", result="pending",
            reason=f"需要审批: {decision.reason}", risk=decision.risk,
            approval_id=approval_id,
            workspace_dir=effective_workspace,
        )

    # 4. Execute (v1: audit only — actual execution is done by tools)
    return _audit_and_return(
        thread_id=thread_id, kind=kind, target=target,
        allowed=True, decision="auto_allowed", result="success",
        reason=f"已执行: {decision.reason}", risk=decision.risk,
        workspace_dir=effective_workspace,
    )


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
) -> dict[str, Any]:
    """Write audit record and return standardized response."""
    action_id = f"act_{uuid.uuid4().hex[:12]}"
    repo = get_audit_repo()
    record = AuditRecord(
        audit_id=f"audit_{uuid.uuid4().hex[:12]}",
        thread_id=thread_id,
        action_id=action_id,
        kind=kind,
        target=target,
        decision=decision,
        result=result,
        reason=reason,
        detail={"risk": risk, "approval_id": approval_id} if approval_id else {"risk": risk},
        created_at=time.time(),
    )
    repo.append(record, workspace_dir)
    return {
        "action_id": action_id,
        "thread_id": thread_id,
        "allowed": allowed,
        "requires_approval": decision == "approved" and result == "pending",
        "reason": reason,
        "risk": risk,
        "approval_id": approval_id,
    }
