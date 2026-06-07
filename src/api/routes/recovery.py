"""Recovery, checkpoints, git, policy, observability, and workspace recovery routes."""

from __future__ import annotations

import queue
import uuid

from fastapi import APIRouter, HTTPException

from src.api.models import (
    GitCommitRequest,
    PolicyDecisionRecordRequest,
    RecoveryActionRequest,
    RemediationRunRequest,
    RollbackRequest,
    RunRestoreRequest,
)
from src.api.run_state import (
    active_runs,
    audit_route_action,
    emit_agenthub_event,
    event_store,
    get_workspace,
    run_manager,
    runs_lock,
    workspace_for_thread,
)
from src.api.services.checkpoint_service import create_checkpoint, list_checkpoints, restore_checkpoint
from src.api.services.git_sandbox_service import commit_branch, discard_branch, git_branch_status, prepare_git_branch
from src.api.services.observability_service import build_run_observability
from src.api.services.recovery_action_service import execute_recovery_action
from src.api.services.recovery_service import build_recovery_center, rollback_from_backup
from src.api.services.run_context import RunContext
from src.api.services.workflow_thread_service import start_workflow_thread

router = APIRouter(tags=["recovery"])


def _get_workspace() -> str:
    return get_workspace()


# --- Recovery ---

@router.get("/api/runs/{thread_id}/recovery")
async def get_run_recovery(thread_id: str):
    return build_recovery_center(thread_id, workspace_for_thread(thread_id))


@router.post("/api/runs/{thread_id}/recovery/actions/{action_id}")
async def run_recovery_action(thread_id: str, action_id: str, request: RecoveryActionRequest):
    workspace = workspace_for_thread(thread_id)
    target = request.target or request.target_path
    try:
        result = execute_recovery_action(
            thread_id=thread_id,
            action_id=action_id,
            workspace_dir=workspace,
            target=target,
            target_path=request.target_path,
            confirmed=request.confirmed,
        )
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="recovery_action",
            target=action_id,
            decision="confirmed" if request.confirmed else "allowed",
            result="success" if result.get("ok", True) else "failure",
            reason=str(result.get("message", "")),
            detail={"action_id": action_id, "target": target, "target_path": request.target_path},
        )
        return result
    except ValueError as exc:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="recovery_action",
            target=action_id,
            decision="denied",
            result="failure",
            reason=str(exc),
            detail={"action_id": action_id, "target": target, "target_path": request.target_path},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Remediation ---

@router.post("/api/runs/{thread_id}/remediation")
async def start_remediation_run(thread_id: str, request: RemediationRunRequest):
    from src.infra.messages import HumanMessage

    sm = run_manager.get_state_machine(thread_id)
    if sm and not sm.current_status.endswith("ed"):
        raise HTTPException(status_code=409, detail="Run 还在活跃中，无法启动修复。")
    new_tid = str(uuid.uuid4())
    q = queue.Queue()
    run_workspace = workspace_for_thread(thread_id)
    prompt = request.instruction.strip() or (
        f"请基于原 run {thread_id} 的失败记录"
        f"{' ' + request.failure_id if request.failure_id else ''} 进行修复。"
    )

    with runs_lock:
        run_context = RunContext(
            thread_id=new_tid,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            team=[],
            execution_plan={},
        )
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=new_tid,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
        mode="remediation",
    )
    event_store.append_event(
        thread_id=new_tid,
        event_type="remediation_started",
        title="修复运行已启动",
        content=prompt,
        agent="lead",
        payload={"original_thread_id": thread_id, "failure_id": request.failure_id},
        workspace_dir=run_workspace,
    )

    initial_messages = [HumanMessage(content=prompt)]
    start_workflow_thread(
        thread_id=new_tid,
        initial_messages=initial_messages,
        workspace_dir=run_workspace,
        run_context=run_context,
    )

    return {"original_thread_id": thread_id, "retry_thread_id": new_tid, "status": "created"}


# --- Checkpoints ---

@router.post("/api/runs/{thread_id}/checkpoints")
async def create_run_checkpoint(thread_id: str, request: RecoveryActionRequest):
    workspace = workspace_for_thread(thread_id)
    target_path = request.target_path or request.target
    if not target_path:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="checkpoint_create",
            target="",
            decision="denied",
            result="failure",
            reason="创建 checkpoint 需要 target_path。",
        )
        raise HTTPException(status_code=400, detail="创建 checkpoint 需要 target_path。")
    try:
        result = create_checkpoint(
            filepath=target_path,
            reason=request.action_id or "manual checkpoint",
            thread_id=thread_id,
            workspace_dir=workspace,
        )
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="checkpoint_create",
            target=target_path,
            decision="auto_allowed",
            result="success",
            reason=request.action_id or "manual checkpoint",
            detail={"checkpoint_id": result.get("checkpoint_id")},
        )
        return result
    except ValueError as exc:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="checkpoint_create",
            target=target_path,
            decision="denied",
            result="failure",
            reason=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/runs/{thread_id}/checkpoints")
async def list_run_checkpoints(thread_id: str):
    return list_checkpoints(thread_id, workspace_for_thread(thread_id))


@router.post("/api/runs/{thread_id}/checkpoints/{checkpoint_id}/restore")
async def restore_run_checkpoint(thread_id: str, checkpoint_id: str, request: RecoveryActionRequest):
    workspace = workspace_for_thread(thread_id)
    try:
        result = restore_checkpoint(
            checkpoint_id=checkpoint_id,
            thread_id=thread_id,
            confirmed=request.confirmed,
            workspace_dir=workspace,
        )
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="checkpoint_restore",
            target=checkpoint_id,
            decision="confirmed",
            result="success",
            reason="checkpoint restored",
            detail={"checkpoint_id": checkpoint_id, "filepath": result.get("filepath")},
        )
        return result
    except ValueError as exc:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="checkpoint_restore",
            target=checkpoint_id,
            decision="confirmed" if request.confirmed else "denied",
            result="failure",
            reason=str(exc),
            detail={"checkpoint_id": checkpoint_id},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/runs/{thread_id}/restore")
async def restore_run(thread_id: str, request: RunRestoreRequest):
    """Restore a run checkpoint by id or by latest checkpoint for target_path."""
    workspace = workspace_for_thread(thread_id)
    if not request.confirmed:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="run_restore",
            target=request.checkpoint_id or request.target_path,
            decision="denied",
            result="failure",
            reason="restore 需要 confirmed=true 确认。",
            detail={"checkpoint_id": request.checkpoint_id, "target_path": request.target_path},
        )
        raise HTTPException(status_code=400, detail="restore 需要 confirmed=true 确认。")

    checkpoint_id = request.checkpoint_id.strip()
    restore_mode = "checkpoint_id"
    if not checkpoint_id:
        target_path = request.target_path.strip()
        if not target_path:
            audit_route_action(
                thread_id=thread_id,
                workspace_dir=workspace,
                kind="run_restore",
                target="",
                decision="denied",
                result="failure",
                reason="restore 需要 checkpoint_id 或 target_path。",
            )
            raise HTTPException(status_code=400, detail="restore 需要 checkpoint_id 或 target_path。")
        checkpoints = list_checkpoints(thread_id, workspace)
        candidates = checkpoints.get("files", {}).get(target_path, [])
        if not candidates:
            audit_route_action(
                thread_id=thread_id,
                workspace_dir=workspace,
                kind="run_restore",
                target=target_path,
                decision="denied",
                result="failure",
                reason=f"未找到 {target_path} 的 checkpoint。",
            )
            raise HTTPException(status_code=404, detail=f"未找到 {target_path} 的 checkpoint。")
        checkpoint_id = str(candidates[0].get("checkpoint_id") or "")
        restore_mode = "latest_for_file"

    try:
        result = restore_checkpoint(
            checkpoint_id=checkpoint_id,
            thread_id=thread_id,
            confirmed=True,
            workspace_dir=workspace,
        )
        response = {
            **result,
            "thread_id": thread_id,
            "restore_mode": restore_mode,
            "requested_target_path": request.target_path,
        }
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="run_restore",
            target=checkpoint_id,
            decision="confirmed",
            result="success",
            reason=str(result.get("message", "")),
            detail={
                "checkpoint_id": checkpoint_id,
                "filepath": result.get("filepath"),
                "restore_mode": restore_mode,
                "requested_target_path": request.target_path,
            },
        )
        event_store.append_event(
            thread_id=thread_id,
            event_type="checkpoint_restored",
            title="Run checkpoint 已恢复",
            content=str(result.get("message", "")),
            agent="system",
            payload={
                "checkpoint_id": checkpoint_id,
                "filepath": result.get("filepath"),
                "restore_mode": restore_mode,
            },
            workspace_dir=workspace,
        )
        return response
    except ValueError as exc:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="run_restore",
            target=checkpoint_id,
            decision="confirmed",
            result="failure",
            reason=str(exc),
            detail={"checkpoint_id": checkpoint_id, "restore_mode": restore_mode},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Git ---

@router.post("/api/runs/{thread_id}/git/prepare")
async def prepare_run_git_branch(thread_id: str):
    workspace = workspace_for_thread(thread_id)
    result = prepare_git_branch(thread_id, workspace)
    audit_route_action(
        thread_id=thread_id,
        workspace_dir=workspace,
        kind="git_operation",
        target="prepare",
        decision="auto_allowed",
        result="success" if result.get("ok") else "failure",
        reason=str(result.get("message", "")),
        detail=result,
    )
    return result


@router.get("/api/runs/{thread_id}/git/status")
async def get_run_git_status(thread_id: str):
    return git_branch_status(thread_id, workspace_for_thread(thread_id))


@router.post("/api/runs/{thread_id}/git/commit")
async def commit_run_branch(thread_id: str, commit_request: GitCommitRequest):
    workspace = workspace_for_thread(thread_id)
    result = commit_branch(thread_id, commit_request.message, workspace)
    audit_route_action(
        thread_id=thread_id,
        workspace_dir=workspace,
        kind="git_operation",
        target="commit",
        decision="confirmed",
        result="success" if result.get("ok") else "failure",
        reason=str(result.get("message", "")),
        detail=result,
    )
    return result


@router.post("/api/runs/{thread_id}/git/discard")
async def discard_run_branch(thread_id: str, request: RecoveryActionRequest):
    workspace = workspace_for_thread(thread_id)
    try:
        result = discard_branch(
            thread_id,
            confirmed=request.confirmed,
            workspace_dir=workspace,
        )
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="git_operation",
            target="discard",
            decision="confirmed",
            result="success" if result.get("ok") else "failure",
            reason=str(result.get("message", "")),
            detail=result,
        )
        return result
    except ValueError as exc:
        audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="git_operation",
            target="discard",
            decision="confirmed" if request.confirmed else "denied",
            result="failure",
            reason=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Policy ---

@router.get("/api/runs/{thread_id}/policy")
async def get_run_policy(thread_id: str):
    return {"thread_id": thread_id, "decisions": []}


@router.post("/api/runs/{thread_id}/policy/decision")
async def record_policy_decision(thread_id: str, request: PolicyDecisionRecordRequest):
    return {"thread_id": thread_id, "decision": request.decision, "status": "recorded"}


# --- Observability ---

@router.get("/api/runs/{thread_id}/observability")
async def get_run_observability(thread_id: str):
    return build_run_observability(thread_id, workspace_for_thread(thread_id))


# --- Workspace Recovery ---

@router.get("/api/recovery")
async def get_recovery_center():
    return build_recovery_center("", _get_workspace())


@router.post("/api/recovery/rollback")
async def rollback_recovery(request: RollbackRequest):
    workspace = _get_workspace()
    target = request.target_path
    if not request.confirmed:
        audit_route_action(
            thread_id="workspace",
            workspace_dir=workspace,
            kind="rollback",
            target=target,
            decision="denied",
            result="failure",
            reason="rollback 需要 confirmed=true 确认。",
            detail={"backup_name": request.backup_name},
        )
        raise HTTPException(status_code=400, detail="rollback 需要 confirmed=true 确认。")
    try:
        result = rollback_from_backup(
            backup_name=request.backup_name,
            target_path=request.target_path,
            workspace_dir=workspace,
        )
        audit_route_action(
            thread_id="workspace",
            workspace_dir=workspace,
            kind="rollback",
            target=target,
            decision="confirmed",
            result="success",
            reason=str(result.get("message", "")),
            detail={"backup_name": request.backup_name},
        )
        return result
    except (FileNotFoundError, ValueError) as exc:
        audit_route_action(
            thread_id="workspace",
            workspace_dir=workspace,
            kind="rollback",
            target=target,
            decision="confirmed",
            result="failure",
            reason=str(exc),
            detail={"backup_name": request.backup_name},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
