"""Run routes: runtime views, delivery contract, changes, ledger, and loop APIs."""

from __future__ import annotations

import queue
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.dependencies import get_workspace, raise_404
from src.api.models import (
    ActionCheckRequest,
    ActionExecuteRequest,
    ChangeSetApproveRequest,
    ChangeSetCollectRequest,
    ChangeSetReviewRequest,
    ContextPackRequest,
    DeliveryFinalizeRequest,
    DeliveryRegenerateRequest,
    EphemeralAgentArchiveRequest,
    EphemeralAgentCompleteRequest,
    EphemeralAgentSpawnRequest,
    EphemeralAgentSuggestRequest,
    IntentCorrectionRequest,
    LoopActionCheckRequest,
    LoopStepRequest,
    RemediationRequest,
    RetryRunRequest,
    RunStatePatchRequest,
    CancelResponse,
    TaskResultRequest,
)
from src.api.services.change_service import (
    approve_changes,
    collect_changes,
    load_change_set,
    review_changes,
)
from src.api.services.delivery_service import (
    finalize_delivery,
    load_delivery_contract,
    regenerate_delivery,
)
from src.api.services.event_store import get_event_store
from src.api.run_state import (
    active_runs,
    emit_agenthub_event,
    emit_stage_updates,
    event_store,
    run_manager,
    runs_lock,
    session_for_thread,
    sync_run_context,
    workspace_for_thread,
)
from src.api.services.run_context import RunContext
from src.api.services.run_history import list_run_history_with_active
from src.api.services.retry_context_service import build_retry_prompt, collect_retry_context
from src.api.services.sse_broker import stream_events_push
from src.api.services.workflow_thread_service import (
    start_resumed_workflow_thread,
    start_workflow_thread,
)
from src.api.services.workspace_registry_service import list_recent_projects
from src.infra.messages import HumanMessage
from src.runtime.run_state import TERMINAL_STATUSES

router = APIRouter(prefix="/api/runs", tags=["runs"])
runtime_router = APIRouter(prefix="/api", tags=["runs"])


def _workspace_for_run(thread_id: str, *, require_session: bool = False) -> str:
    """Resolve the workspace associated with a run before reading run artifacts."""
    store = event_store

    indexed_workspace = store.workspace_for_thread(thread_id)
    if indexed_workspace and store.get_session(thread_id, indexed_workspace):
        return indexed_workspace

    current_workspace = get_workspace()
    session = store.get_session(thread_id, current_workspace)
    if session:
        return session.get("workspace_dir") or current_workspace

    for item in list_recent_projects():
        workspace = item.get("path")
        if workspace and store.get_session(thread_id, workspace):
            return workspace

    if require_session:
        raise_404(f"Run {thread_id} not found")
    return current_workspace


@runtime_router.get("/runs")
async def list_agenthub_runs(limit: int = 50, workspace_dir: str | None = None):
    workspace = workspace_dir or get_workspace()
    return {
        "runs": list_run_history_with_active(
            run_manager,
            workspace,
            limit=min(max(limit, 1), 200),
        )
    }


@runtime_router.get("/runs/active")
async def list_active_runs():
    return {"active_runs": run_manager.list_active()}


@runtime_router.get("/runs/{thread_id}")
async def get_agenthub_run_detail(thread_id: str):
    with runs_lock:
        run_info = active_runs.get(thread_id)
    if run_info:
        return {
            "thread_id": thread_id,
            "status": run_info.get("status"),
            "workspace_dir": run_info.get("workspace_dir", get_workspace()),
            "conversation_id": run_info.get("conversation_id"),
        }
    session = session_for_thread(thread_id)
    if session:
        return session
    raise HTTPException(status_code=404, detail="Run 不存在")


@runtime_router.get("/runs/{thread_id}/events/history")
async def get_agenthub_run_events_history(thread_id: str):
    events = event_store.list_events(thread_id, workspace_for_thread(thread_id))
    return {"events": [event.model_dump() for event in events]}


@runtime_router.get("/runs/{thread_id}/events")
async def get_run_events_push(thread_id: str, request: Request):
    workspace_dir = workspace_for_thread(thread_id)
    return StreamingResponse(
        stream_events_push(
            thread_id,
            workspace_dir,
            last_event_id=request.headers.get("last-event-id"),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _cancel_run_impl(thread_id: str):
    with runs_lock:
        run_info = active_runs.get(thread_id)

    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    current_status = run_info.get("status")
    if current_status == "cancelling":
        return CancelResponse(cancelled=True, thread_id=thread_id)
    if current_status not in {"running", "waiting_approval"}:
        raise HTTPException(status_code=400, detail=f"工作流状态为 {run_info.get('status')}，无法取消")

    try:
        run_manager.request_cancel(thread_id)
    except ValueError:
        pass
    run_info.set_status("cancelling")
    workspace_dir = run_info.get("workspace_dir") or workspace_for_thread(thread_id)
    sync_run_context(thread_id, workspace_dir)
    event_store.update_session(thread_id, workspace_dir, status="cancelling")
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_cancelling",
        title="正在取消运行",
        content="用户请求取消运行，Agent 会在下一个安全检查点停止。",
        agent="lead",
        payload={"status": "cancelling"},
        workspace_dir=workspace_dir,
    )
    return CancelResponse(cancelled=True, thread_id=thread_id)


@runtime_router.post("/runs/{thread_id}/cancel")
async def cancel_run(thread_id: str):
    return await _cancel_run_impl(thread_id)


@runtime_router.get("/runs/{thread_id}/runtime-state")
async def get_run_runtime_state(thread_id: str):
    sm = run_manager.get_state_machine(thread_id)
    if not sm:
        raise HTTPException(status_code=404, detail=f"Run 不在活跃列表中: {thread_id}")
    return {"thread_id": thread_id, "state": sm.to_dict()}


@runtime_router.post("/runs/{thread_id}/resume")
async def resume_run(thread_id: str):
    sm = run_manager.get_state_machine(thread_id)
    if sm:
        raise HTTPException(status_code=409, detail="Run 已在活跃列表中。")

    session = session_for_thread(thread_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Run 不存在: {thread_id}")

    resumable_statuses = {"interrupted", "failed", "cancelled"}
    if session.get("status") not in resumable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"只能恢复 interrupted/failed/cancelled 状态的 run，当前状态: {session.get('status')}",
        )

    saved_messages = session.get("saved_messages")
    if not saved_messages:
        raise HTTPException(status_code=400, detail="该 run 没有保存的消息历史，无法恢复。")

    workspace_dir = session.get("workspace_dir") or workspace_for_thread(thread_id)
    previous_status = session.get("status")

    run_context = RunContext(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        queue=queue.Queue(),
        status="running",
        conversation_id=session.get("conversation_id"),
    )
    with runs_lock:
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.update_session(thread_id, workspace_dir, status="running")
    event_store.append_event(
        thread_id,
        "run_resumed",
        title="Run 已恢复",
        content=f"从 {previous_status} 状态恢复运行，加载了 {len(saved_messages)} 条历史消息。",
        agent="lead",
        payload={"previous_status": previous_status, "message_count": len(saved_messages)},
        workspace_dir=workspace_dir,
    )

    execution_plan = session.get("execution_plan", {})
    run_team = session.get("team", [])
    strategy = execution_plan.get("strategy", "feature_delivery")
    from src.agent.prompt_builder import _build_core
    from src.api.services.orchestration_service import build_runtime_instructions

    system = _build_core(strategy)
    system = f"{system}\n\n注意：工作目录已经是 {workspace_dir}，写文件名时直接用文件名，不要加 workspace/ 前缀。"
    runtime_instructions = build_runtime_instructions(execution_plan, run_team)
    if runtime_instructions:
        system = f"{system}\n{runtime_instructions}"

    start_resumed_workflow_thread(
        thread_id=thread_id,
        messages=saved_messages,
        system=system,
        workspace_dir=workspace_dir,
        run_context=run_context,
    )

    return {"thread_id": thread_id, "status": "running", "ok": True, "resumed_from": previous_status}


@runtime_router.get("/runs/{thread_id}/lifecycle")
async def get_run_lifecycle(thread_id: str):
    """Get the full lifecycle status, history, and stage progress of a run."""
    sm = run_manager.get_state_machine(thread_id)
    session = session_for_thread(thread_id)
    if not sm and not session:
        raise HTTPException(status_code=404, detail=f"Run 不存在: {thread_id}")

    session_status = str((session or {}).get("status", "unknown"))
    status = sm.status.value if sm else session_status
    terminal_statuses = {item.value for item in TERMINAL_STATUSES}
    is_terminal = sm.is_terminal() if sm else status in terminal_statuses
    return {
        "thread_id": thread_id,
        "status": status,
        "history": sm.history() if sm else [],
        "is_terminal": is_terminal,
        "session": session or {},
    }


@runtime_router.post("/runs/{thread_id}/retry")
async def retry_run(thread_id: str, request: RetryRunRequest | None = None):
    """Create a new run from a failed/cancelled previous run."""
    session = session_for_thread(thread_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"原 run 不存在: {thread_id}")

    original_status = session.get("status", "")
    if original_status not in ("failed", "cancelled", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"只能重试已结束的 run，当前状态: {original_status}",
        )

    request = request or RetryRunRequest()
    retry_mode = request.retry_mode if request.retry_mode in {"full", "failed_stage"} else "full"
    new_thread_id = str(uuid.uuid4())
    original_prompt = session.get("prompt", "")
    run_workspace = session.get("workspace_dir", get_workspace())
    team = session.get("team", [])
    execution_plan = session.get("execution_plan", {})
    retry_context = collect_retry_context(
        thread_id=thread_id,
        workspace_dir=run_workspace,
        event_store=event_store,
        session=session,
        failure_id=request.failure_id,
    )
    prompt = build_retry_prompt(
        original_prompt=original_prompt,
        original_thread_id=thread_id,
        original_status=original_status,
        retry_mode=retry_mode,
        retry_context=retry_context,
        instruction=request.instruction,
    )

    run_context = RunContext(
        thread_id=new_thread_id,
        workspace_dir=run_workspace,
        queue=queue.Queue(),
        status="running",
        mode="retry",
        team=team,
        execution_plan=execution_plan,
    )
    with runs_lock:
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=new_thread_id,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
        mode="retry",
    )
    event_store.update_session(
        new_thread_id,
        run_workspace,
        **run_context.session_metadata(),
        original_thread_id=thread_id,
        original_status=original_status,
        original_prompt=original_prompt,
        retry_mode=retry_mode,
        retry_context=retry_context,
    )
    stage_updates = run_context.start_first_stage()
    sync_run_context(new_thread_id, run_workspace)
    emit_stage_updates(new_thread_id, run_workspace, stage_updates)
    event_store.append_event(
        thread_id=new_thread_id,
        event_type="run_retried",
        title="Run 已重试",
        content=f"基于原 run {thread_id} 创建 {retry_mode} 重试。",
        agent="lead",
        payload={
            "original_thread_id": thread_id,
            "original_status": original_status,
            "retry_mode": retry_mode,
            "failure_id": request.failure_id or "",
            "failed_stage_id": retry_context.get("failed_stage_id", ""),
        },
        workspace_dir=run_workspace,
    )

    start_workflow_thread(
        thread_id=new_thread_id,
        initial_messages=[HumanMessage(content=prompt)],
        workspace_dir=run_workspace,
        run_context=run_context,
    )

    return {
        "original_thread_id": thread_id,
        "retry_thread_id": new_thread_id,
        "status": "created",
        "retry_mode": retry_mode,
    }


@router.get("/{thread_id}/delivery")
async def get_delivery(thread_id: str):
    """Return the delivery contract for a run."""
    workspace = _workspace_for_run(thread_id, require_session=True)

    contract = load_delivery_contract(thread_id, workspace)
    if contract is None:
        contract = regenerate_delivery(thread_id, workspace, include_markdown=False)
    return contract.model_dump()


@router.post("/{thread_id}/delivery/finalize")
async def post_delivery_finalize(thread_id: str, request: DeliveryFinalizeRequest):
    """Build and persist the delivery contract for a terminal run."""
    workspace = _workspace_for_run(thread_id, require_session=not request.force)
    contract = finalize_delivery(thread_id, workspace, force=request.force)
    if contract is None:
        raise_404(f"Cannot finalize delivery for run {thread_id}: run not found or not terminal")
    return contract.model_dump()


@router.post("/{thread_id}/delivery/regenerate")
async def post_delivery_regenerate(thread_id: str, request: DeliveryRegenerateRequest):
    """Force-regenerate the delivery contract from current run data."""
    workspace = _workspace_for_run(thread_id, require_session=True)
    contract = regenerate_delivery(thread_id, workspace, include_markdown=request.include_markdown)
    return contract.model_dump()


# ---------------------------------------------------------------------------
# R2: Change Set
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/changes")
async def get_changes(thread_id: str):
    """Return the current change set for a run."""
    workspace = _workspace_for_run(thread_id, require_session=True)
    cs = load_change_set(thread_id, workspace)
    if cs is None:
        cs = collect_changes(thread_id, workspace)
    return cs.model_dump()


@router.post("/{thread_id}/changes/collect")
async def post_changes_collect(thread_id: str, request: ChangeSetCollectRequest):
    """Scan workspace diff and generate a change set."""
    workspace = _workspace_for_run(thread_id, require_session=True)
    cs = collect_changes(thread_id, workspace, include_untracked=request.include_untracked)
    return cs.model_dump()


@router.post("/{thread_id}/changes/review")
async def post_changes_review(thread_id: str, request: ChangeSetReviewRequest):
    """Run rule-based risk assessment on the change set."""
    workspace = _workspace_for_run(thread_id, require_session=True)
    cs = review_changes(thread_id, workspace)
    return cs.model_dump()


@router.post("/{thread_id}/changes/approve")
async def post_changes_approve(thread_id: str, request: ChangeSetApproveRequest):
    """Approve or reject the change set."""
    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        cs = approve_changes(thread_id, request.approved, request.comment, workspace)
        return cs.model_dump()
    except ValueError:
        raise_404(f"No change set found for run {thread_id}. Use POST collect first.")


# ---------------------------------------------------------------------------
# R3: Run Ledger
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/ledger")
async def get_ledger(thread_id: str):
    """Return the unified run ledger (steps + tools + approvals + delivery)."""
    from src.api.services.run_ledger_service import get_run_ledger

    workspace = _workspace_for_run(thread_id, require_session=True)
    ledger = get_run_ledger(thread_id, workspace)
    if ledger is None:
        raise_404(f"Run {thread_id} not found in ledger")
    return ledger.model_dump()


@router.get("/{thread_id}/steps")
async def get_steps(thread_id: str):
    """Return step records for a run."""
    from src.api.services.run_ledger_service import get_run_steps

    workspace = _workspace_for_run(thread_id)
    steps = get_run_steps(thread_id, workspace)
    return {"thread_id": thread_id, "steps": [s.model_dump() for s in steps], "total": len(steps)}


@router.get("/{thread_id}/tools")
async def get_tools(thread_id: str):
    """Return deduplicated tool call records for a run."""
    from src.api.services.run_ledger_service import get_run_tools

    workspace = _workspace_for_run(thread_id)
    tools = get_run_tools(thread_id, workspace)
    return {"thread_id": thread_id, "tools": [t.model_dump() for t in tools], "total": len(tools)}


@router.post("/{thread_id}/intent/correct")
async def post_intent_correction(thread_id: str, request: IntentCorrectionRequest):
    """Correct a run's normalized intent decision and record an audit event."""
    from src.api.services.intent_correction_service import correct_run_intent

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        return correct_run_intent(
            thread_id,
            workspace,
            route=request.route,
            complexity=request.complexity,
            reason=request.reason,
            evidence=request.evidence,
            source=request.source,
        )
    except ValueError as exc:
        raise_404(str(exc))


# ---------------------------------------------------------------------------
# R4: Failure Classification & Remediation
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/failures")
async def get_failures(thread_id: str):
    """Return all failure records for a run."""
    from src.api.services.failure_classifier_service import load_failures, save_failures

    workspace = _workspace_for_run(thread_id, require_session=True)
    failures = load_failures(thread_id, workspace)
    if not failures:
        # Auto-classify on first access
        failures = save_failures(thread_id, workspace)
    return {"thread_id": thread_id, "failures": [f.model_dump() for f in failures], "total": len(failures)}


@router.get("/{thread_id}/failures/{failure_id}")
async def get_failure(thread_id: str, failure_id: str):
    """Return a single failure record."""
    from src.api.services.failure_classifier_service import load_failures

    workspace = _workspace_for_run(thread_id, require_session=True)
    failures = load_failures(thread_id, workspace)
    for f in failures:
        if f.failure_id == failure_id:
            return f.model_dump()
    raise_404(f"Failure {failure_id} not found in run {thread_id}")


@router.post("/{thread_id}/failures/{failure_id}/remediate")
async def post_remediate(thread_id: str, failure_id: str, request: RemediationRequest):
    """Create a remediation plan or retry run for a failure."""
    from src.api.services.remediation_planner_service import create_remediation_run, plan_remediation

    workspace = _workspace_for_run(thread_id, require_session=True)
    plan = plan_remediation(failure_id, thread_id, workspace)
    if plan is None:
        raise_404(f"Failure {failure_id} not found in run {thread_id}")

    if request.mode == "manual":
        return {"plan": plan, "created": False}

    result = create_remediation_run(thread_id, failure_id, mode=request.mode, workspace_dir=workspace)
    return result


# ---------------------------------------------------------------------------
# R5: Action Policy & Audit
# ---------------------------------------------------------------------------


@router.post("/{thread_id}/actions/check")
async def post_action_check(thread_id: str, request: ActionCheckRequest):
    """Pre-flight check: is this action allowed? Does it need approval?"""
    from src.api.services.action_execution_service import check_and_decide
    workspace = _workspace_for_run(thread_id)

    return check_and_decide(
        kind=request.kind,
        target=request.target,
        thread_id=thread_id,
        workspace_dir=workspace,
        payload=request.payload,
    )


@router.post("/{thread_id}/actions/execute")
async def post_action_execute(thread_id: str, request: ActionExecuteRequest):
    """Execute an action through the unified pipeline."""
    from src.api.services.action_execution_service import execute_action_async
    workspace = _workspace_for_run(thread_id)
    payload = dict(request.payload or {})
    if request.approval_id:
        payload["approval_id"] = request.approval_id

    return await execute_action_async(
        kind=request.kind,
        target=request.target,
        payload=payload,
        thread_id=thread_id,
        workspace_dir=workspace,
    )


@router.get("/{thread_id}/audit")
async def get_audit_trail(thread_id: str):
    """Return the audit trail for a run."""
    from src.api.services.action_execution_service import get_audit_trail
    workspace = _workspace_for_run(thread_id)

    return get_audit_trail(thread_id, workspace)


# ---------------------------------------------------------------------------
# R5.25: Run Snapshot
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/snapshot")
async def get_run_snapshot(thread_id: str):
    """Return the read-only frontend aggregate snapshot for one run."""
    from src.api.services.run_snapshot_service import build_run_snapshot

    workspace = _workspace_for_run(thread_id, require_session=True)
    return build_run_snapshot(thread_id, workspace).model_dump()


# ---------------------------------------------------------------------------
# R5.5: Context Pack & Agent Loop Run State
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/context-pack")
async def get_context_pack(thread_id: str):
    """Build and return the run-level ContextPack."""
    from src.api.services.run_state_service import build_run_context_pack

    workspace = _workspace_for_run(thread_id, require_session=True)
    return build_run_context_pack(thread_id, workspace)


@router.get("/{thread_id}/context-pack/debug")
async def get_context_pack_debug(thread_id: str):
    """Return ContextPack debug information, including file selection reasons."""
    from src.api.services.run_state_service import build_run_context_pack

    workspace = _workspace_for_run(thread_id, require_session=True)
    pack = build_run_context_pack(thread_id, workspace)
    return {
        "thread_id": thread_id,
        "workspace_dir": workspace,
        "selected_files": pack.get("selected_files", []),
        "token_budget": pack.get("token_budget", {}),
        "context_debug": pack.get("context_debug", {}),
    }


@router.get("/{thread_id}/context-packs")
async def get_context_packs(thread_id: str):
    """List persisted ContextPack snapshots for one run."""
    from src.api.services.run_state_service import list_context_packs

    workspace = _workspace_for_run(thread_id, require_session=True)
    return list_context_packs(thread_id, workspace)


@router.get("/{thread_id}/context-packs/{pack_id}")
async def get_context_pack_by_id_route(thread_id: str, pack_id: str):
    """Return one persisted ContextPack snapshot."""
    from src.api.services.run_state_service import get_context_pack_by_id

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        return get_context_pack_by_id(thread_id, workspace, pack_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.post("/{thread_id}/context-packs/preview")
async def post_context_pack_preview(thread_id: str, request: ContextPackRequest | None = None):
    """Build an unsaved ContextPack preview for the current run."""
    from src.api.services.run_state_service import preview_context_pack

    workspace = _workspace_for_run(thread_id, require_session=True)
    return preview_context_pack(thread_id, workspace, objective=request.objective if request else "")


@router.post("/{thread_id}/context-pack/rebuild")
async def post_context_pack_rebuild(thread_id: str):
    """Force-rebuild and persist the run-level ContextPack."""
    from src.api.services.run_state_service import build_run_context_pack

    workspace = _workspace_for_run(thread_id, require_session=True)
    return build_run_context_pack(thread_id, workspace)


@router.post("/{thread_id}/summaries/refresh")
async def post_summaries_refresh(thread_id: str):
    """Refresh execution summary from run state and events."""
    from src.api.services.run_state_service import refresh_summaries

    workspace = _workspace_for_run(thread_id, require_session=True)
    return refresh_summaries(thread_id, workspace)


@router.get("/{thread_id}/state")
async def get_run_state(thread_id: str):
    """Return the Agent Loop friendly mutable run state."""
    from src.api.services.run_state_service import get_run_task_board

    workspace = _workspace_for_run(thread_id, require_session=True)
    return get_run_task_board(thread_id, workspace)


@router.get("/{thread_id}/loop")
async def get_run_loop_state(thread_id: str):
    """Return the persistent Lead Agent loop ledger for one run."""
    from src.api.services.agent_loop_state_service import get_agent_loop_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    return get_agent_loop_state(thread_id, workspace)


@router.post("/{thread_id}/loop/actions/check")
async def post_run_loop_action_check(thread_id: str, request: LoopActionCheckRequest):
    """Dry-run a structured Lead action before it is appended to the loop ledger."""
    from src.api.services.agent_loop_state_service import check_loop_action

    workspace = _workspace_for_run(thread_id, require_session=True)
    return check_loop_action(thread_id, workspace, request.action)


@router.get("/{thread_id}/loop/observation")
async def get_run_loop_observation(thread_id: str):
    """Return the read-only observation used by the Agent Loop controller."""
    from src.api.services.agent_loop_controller_service import get_loop_observation

    workspace = _workspace_for_run(thread_id, require_session=True)
    return get_loop_observation(thread_id, workspace)


@router.post("/{thread_id}/loop/step")
async def post_run_loop_step(thread_id: str, request: LoopStepRequest):
    """Preview or commit one Agent Loop controller step."""
    from src.api.services.agent_loop_controller_service import run_loop_controller_step

    workspace = _workspace_for_run(thread_id, require_session=True)
    return run_loop_controller_step(
        thread_id,
        workspace,
        action=request.action,
        commit=request.commit,
        auto_repair=request.auto_repair,
        execute_tools=request.execute_tools,
    )


@router.patch("/{thread_id}/state")
async def patch_run_state(thread_id: str, request: RunStatePatchRequest):
    """Patch mutable run state. Used by Lead loop to revise its task board."""
    from src.api.services.run_state_service import patch_run_state as apply_patch

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        board = apply_patch(thread_id, workspace, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return board.to_task_board()


@router.get("/{thread_id}/tasks")
async def get_run_tasks(thread_id: str):
    """Return run-scoped tasks without creating a task board as a read side effect."""
    from src.api.services.run_state_service import get_run_tasks_readonly

    workspace = _workspace_for_run(thread_id, require_session=True)
    return get_run_tasks_readonly(thread_id, workspace)


@router.patch("/{thread_id}/tasks")
async def patch_run_tasks(thread_id: str, request: RunStatePatchRequest):
    """Patch the mutable task board through the run-scoped task alias."""
    from src.api.services.run_state_service import patch_run_state as apply_patch

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        board = apply_patch(thread_id, workspace, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return board.to_task_board()


@router.get("/{thread_id}/tasks/{task_id}")
async def get_run_task(thread_id: str, task_id: str):
    """Return one run-scoped task from the read-only task view."""
    from src.api.services.run_state_service import get_run_tasks_readonly

    workspace = _workspace_for_run(thread_id, require_session=True)
    tasks = get_run_tasks_readonly(thread_id, workspace)
    task = next((item for item in tasks.get("tasks", []) if item.get("id") == task_id), None)
    if not task:
        raise_404(f"Task {task_id} not found in run {thread_id}")
    return task


@router.post("/{thread_id}/tasks/{task_id}/retry")
async def post_run_task_retry(thread_id: str, task_id: str):
    """Mark one run-scoped task ready for retry."""
    from src.api.services.run_state_service import update_task_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_task_status(thread_id, workspace, task_id, "ready")
    except ValueError as exc:
        raise_404(str(exc))
    return state.to_task_board()


@router.get("/{thread_id}/state/schedule")
async def get_run_state_schedule(thread_id: str, parallel_limit: int = 3):
    """Preview the next safe task-board batch for the Agent loop."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.run_scheduler import preview_next_batch

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    return preview_next_batch(state, parallel_limit=parallel_limit).model_dump()


@router.get("/{thread_id}/state/tasks")
async def get_run_state_tasks(thread_id: str):
    """Return all task-board tasks."""
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    return {"thread_id": thread_id, "tasks": [node.model_dump() for node in state.nodes]}


@router.get("/{thread_id}/state/tasks/{task_id}")
async def get_run_state_task(thread_id: str, task_id: str):
    """Return one task-board task."""
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    task = state.node(task_id)
    if not task:
        raise_404(f"Task {task_id} not found in run {thread_id}")
    return task.model_dump()


@router.get("/{thread_id}/state/tasks/{task_id}/context")
async def get_run_state_task_context(thread_id: str, task_id: str):
    """Build and return ContextPack for one task-board task."""
    from src.api.services.run_state_service import build_task_context_pack

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        return build_task_context_pack(thread_id, workspace, task_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/{thread_id}/state/tasks/{task_id}/evidence")
async def get_run_state_task_evidence(thread_id: str, task_id: str):
    """Return event evidence associated with one task-board task."""
    from src.api.services.run_state_service import get_task_evidence

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        return get_task_evidence(thread_id, workspace, task_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.post("/{thread_id}/state/rebuild")
async def post_run_state_rebuild(thread_id: str):
    """Rebuild the mutable task board from the stored execution plan."""
    from src.api.services.run_state_service import rebuild_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = rebuild_run_state(thread_id, workspace, reason="api_state_rebuild")
    return state.to_task_board()


@router.post("/{thread_id}/state/tasks/{task_id}/retry")
async def post_run_state_task_retry(thread_id: str, task_id: str):
    """Mark one task-board task ready for retry."""
    from src.api.services.run_state_service import update_task_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_task_status(thread_id, workspace, task_id, "ready")
    except ValueError as exc:
        raise_404(str(exc))
    return state.to_task_board()


@router.post("/{thread_id}/state/tasks/{task_id}/start")
async def post_run_state_task_start(thread_id: str, task_id: str):
    """Mark a task-board task running and acquire its locks."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import save_task_board
    from src.runtime.run_scheduler import mark_task_running

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    try:
        state = mark_task_running(state, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_task_board(state, get_event_store().run_dir(thread_id, workspace))
    get_event_store().append_event(
        thread_id,
        "task_started",
        title=f"任务开始：{task_id}",
        content=f"{task_id} started.",
        agent="lead",
        payload={"node_id": task_id, "task_id": task_id},
        workspace_dir=workspace,
    )
    return state.to_task_board()


@router.post("/{thread_id}/state/tasks/{task_id}/result")
async def post_run_state_task_result(
    thread_id: str,
    task_id: str,
    request: TaskResultRequest,
):
    """Apply a task-board task result and release locks."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import save_task_board
    from src.runtime.run_scheduler import TaskExecutionResult, apply_task_result

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    try:
        result = TaskExecutionResult(
            task_id=task_id,
            status=request.status,  # type: ignore[arg-type]
            summary=request.summary,
            evidence=request.evidence,
            outputs=request.outputs,
            failure_category=request.failure_category,
            retryable=request.retryable,
        )
        state = apply_task_result(state, result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_task_board(state, get_event_store().run_dir(thread_id, workspace))
    get_event_store().append_event(
        thread_id,
        f"task_{request.status}",
        title=f"任务结果：{task_id}",
        content=request.summary,
        agent="lead",
        payload={"node_id": task_id, "task_id": task_id, "status": request.status},
        workspace_dir=workspace,
    )
    return state.to_task_board()


@router.post("/{thread_id}/state/tasks/{task_id}/skip")
async def post_run_state_task_skip(thread_id: str, task_id: str):
    """Skip one task-board task."""
    from src.api.services.run_state_service import update_task_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_task_status(thread_id, workspace, task_id, "skipped")
    except ValueError as exc:
        raise_404(str(exc))
    return state.to_task_board()


@router.post("/{thread_id}/state/tasks/{task_id}/approve")
async def post_run_state_task_approve(thread_id: str, task_id: str):
    """Approve/pass a gate-like task-board task."""
    from src.api.services.run_state_service import update_task_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_task_status(thread_id, workspace, task_id, "passed")
    except ValueError as exc:
        raise_404(str(exc))
    return state.to_task_board()


@router.post("/{thread_id}/state/cancel")
async def post_run_state_cancel(thread_id: str):
    """Cancel the task board by marking active tasks cancelled."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import save_task_board

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    for node in state.nodes:
        if node.status in {"pending", "ready", "running", "blocked"}:
            node.status = "cancelled"
    state.status = "cancelled"
    save_task_board(state, get_event_store().run_dir(thread_id, workspace))
    get_event_store().append_event(
        thread_id,
        "run_state_cancelled",
        title="运行状态已取消",
        content="Task board cancelled by API.",
        agent="lead",
        payload={"node_count": len(state.nodes)},
        workspace_dir=workspace,
    )
    return state.to_task_board()


@router.get("/{thread_id}/graph")
async def get_run_graph(thread_id: str):
    """Legacy alias: return the structured mutable task board."""
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    return state.model_dump()


@router.get("/{thread_id}/graph/nodes")
async def get_run_graph_nodes(thread_id: str):
    """Legacy alias: return all run-state nodes."""
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    return {"thread_id": thread_id, "nodes": [node.model_dump() for node in state.nodes]}


@router.get("/{thread_id}/graph/schedule")
async def get_run_graph_schedule(thread_id: str, parallel_limit: int = 3):
    """Legacy alias: preview the next safe run-state batch."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.run_scheduler import preview_next_batch

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    return preview_next_batch(state, parallel_limit=parallel_limit).model_dump()


@router.get("/{thread_id}/graph/nodes/{node_id}")
async def get_run_graph_node(thread_id: str, node_id: str):
    """Legacy alias: return one run-state node."""
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    node = state.node(node_id)
    if not node:
        raise_404(f"Node {node_id} not found in run {thread_id}")
    return node.model_dump()


@router.get("/{thread_id}/graph/nodes/{node_id}/context")
async def get_run_graph_node_context(thread_id: str, node_id: str):
    """Build and return ContextPack for one run-state task."""
    from src.api.services.run_state_service import build_task_context_pack

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        return build_task_context_pack(thread_id, workspace, node_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/{thread_id}/graph/nodes/{node_id}/evidence")
async def get_run_graph_node_evidence(thread_id: str, node_id: str):
    """Return event evidence associated with one run-state task."""
    from src.api.services.run_state_service import get_task_evidence

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        return get_task_evidence(thread_id, workspace, node_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.post("/{thread_id}/graph/replan")
async def post_run_graph_replan(thread_id: str):
    """Legacy alias: rebuild the mutable task board from the stored execution plan."""
    from src.api.services.run_state_service import rebuild_run_state

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = rebuild_run_state(thread_id, workspace, reason="api_replan")
    return state.model_dump()


@router.post("/{thread_id}/graph/nodes/{node_id}/retry")
async def post_run_graph_node_retry(thread_id: str, node_id: str):
    """Mark one run-state task ready for retry."""
    from src.api.services.run_state_service import update_node_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_node_status(thread_id, workspace, node_id, "ready")
    except ValueError as exc:
        raise_404(str(exc))
    return state.model_dump()


@router.post("/{thread_id}/graph/nodes/{node_id}/start")
async def post_run_graph_node_start(thread_id: str, node_id: str):
    """Mark a run-state node running and acquire its locks."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import save_task_board
    from src.runtime.run_scheduler import mark_task_running

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    try:
        state = mark_task_running(state, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_task_board(state, get_event_store().run_dir(thread_id, workspace))
    get_event_store().append_event(
        thread_id,
        "task_started",
        title=f"任务开始：{node_id}",
        content=f"{node_id} started.",
        agent="lead",
        payload={"node_id": node_id, "task_id": node_id},
        workspace_dir=workspace,
    )
    return state.model_dump()


@router.post("/{thread_id}/graph/nodes/{node_id}/result")
async def post_run_graph_node_result(
    thread_id: str,
    node_id: str,
    request: TaskResultRequest,
):
    """Apply a run-state node result and release locks."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import save_task_board
    from src.runtime.run_scheduler import TaskExecutionResult, apply_task_result

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    try:
        result = TaskExecutionResult(
            task_id=node_id,
            status=request.status,  # type: ignore[arg-type]
            summary=request.summary,
            evidence=request.evidence,
            outputs=request.outputs,
            failure_category=request.failure_category,
            retryable=request.retryable,
        )
        state = apply_task_result(state, result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_task_board(state, get_event_store().run_dir(thread_id, workspace))
    get_event_store().append_event(
        thread_id,
        f"task_{request.status}",
        title=f"任务结果：{node_id}",
        content=request.summary,
        agent="lead",
        payload={"node_id": node_id, "task_id": node_id, "status": request.status},
        workspace_dir=workspace,
    )
    return state.model_dump()


@router.post("/{thread_id}/graph/nodes/{node_id}/skip")
async def post_run_graph_node_skip(thread_id: str, node_id: str):
    """Skip one run-state task and unlock dependents if possible."""
    from src.api.services.run_state_service import update_node_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_node_status(thread_id, workspace, node_id, "skipped")
    except ValueError as exc:
        raise_404(str(exc))
    return state.model_dump()


@router.post("/{thread_id}/graph/nodes/{node_id}/approve")
async def post_run_graph_node_approve(thread_id: str, node_id: str):
    """Approve/pass a gate-like run-state task."""
    from src.api.services.run_state_service import update_node_status

    workspace = _workspace_for_run(thread_id, require_session=True)
    try:
        state = update_node_status(thread_id, workspace, node_id, "passed")
    except ValueError as exc:
        raise_404(str(exc))
    return state.model_dump()


@router.post("/{thread_id}/graph/cancel")
async def post_run_graph_cancel(thread_id: str):
    """Cancel the run state by marking currently active tasks cancelled."""
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import save_task_board

    workspace = _workspace_for_run(thread_id, require_session=True)
    state = get_or_create_run_state(thread_id, workspace)
    for node in state.nodes:
        if node.status in {"pending", "ready", "running", "blocked"}:
            node.status = "cancelled"
    state.status = "cancelled"
    save_task_board(state, get_event_store().run_dir(thread_id, workspace))
    get_event_store().append_event(
        thread_id,
        "run_state_cancelled",
        title="运行状态已取消",
        content="Run state cancelled by API.",
        agent="lead",
        payload={"node_count": len(state.nodes)},
        workspace_dir=workspace,
    )
    return state.model_dump()


# ---------------------------------------------------------------------------
# R6: Ephemeral Agents
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/agents")
async def get_ephemeral_agents(thread_id: str, include_archived: bool = False):
    """Return temporary sub-agents for a run."""
    from src.api.services.ephemeral_agent_service import list_ephemeral_agents

    workspace = _workspace_for_run(thread_id)
    return list_ephemeral_agents(thread_id, workspace, include_archived=include_archived)


@router.post("/{thread_id}/agents/suggest")
async def post_ephemeral_agents_suggest(thread_id: str, request: EphemeralAgentSuggestRequest):
    """Suggest task-scoped temporary sub-agents for a run."""
    from src.api.services.ephemeral_agent_service import suggest_ephemeral_agents

    workspace = _workspace_for_run(thread_id)
    result = suggest_ephemeral_agents(
        request.prompt,
        mcp_plan=request.mcp_plan,
        workspace_dir=workspace,
        max_agents=request.max_agents,
    )
    return {"thread_id": thread_id, **result}


@router.post("/{thread_id}/agents/spawn")
async def post_ephemeral_agent_spawn(thread_id: str, request: EphemeralAgentSpawnRequest):
    """Activate one temporary sub-agent for a run."""
    from src.api.services.ephemeral_agent_service import spawn_ephemeral_agent

    workspace = _workspace_for_run(thread_id)
    try:
        agent = spawn_ephemeral_agent(thread_id, request.agent, workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"thread_id": thread_id, "agent": agent, "ok": True}


@router.post("/{thread_id}/agents/{agent_id}/complete")
async def post_ephemeral_agent_complete(
    thread_id: str,
    agent_id: str,
    request: EphemeralAgentCompleteRequest,
):
    """Complete and auto-archive one temporary sub-agent."""
    from src.api.services.ephemeral_agent_service import complete_ephemeral_agent

    workspace = _workspace_for_run(thread_id)
    try:
        agent = complete_ephemeral_agent(thread_id, agent_id, request.model_dump(), workspace)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc)) from exc
    return {"thread_id": thread_id, "agent": agent, "ok": True}


@router.post("/{thread_id}/agents/{agent_id}/archive")
async def post_ephemeral_agent_archive(
    thread_id: str,
    agent_id: str,
    request: EphemeralAgentArchiveRequest,
):
    """Archive one temporary sub-agent."""
    from src.api.services.ephemeral_agent_service import archive_ephemeral_agent

    workspace = _workspace_for_run(thread_id)
    try:
        agent = archive_ephemeral_agent(thread_id, agent_id, request.reason or "用户归档。", workspace)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc)) from exc
    return {"thread_id": thread_id, "agent": agent, "ok": True}


@router.post("/{thread_id}/agents/cleanup")
async def post_ephemeral_agents_cleanup(thread_id: str):
    """Archive expired temporary sub-agents for a run."""
    from src.api.services.ephemeral_agent_service import cleanup_expired_ephemeral_agents

    workspace = _workspace_for_run(thread_id)
    return cleanup_expired_ephemeral_agents(thread_id, workspace)
