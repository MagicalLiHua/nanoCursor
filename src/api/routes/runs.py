"""Run routes: delivery contract, changes, ledger (R1-R3)."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import get_workspace, raise_404
from src.api.models import (
    ActionCheckRequest,
    ActionExecuteRequest,
    ChangeSetApproveRequest,
    ChangeSetCollectRequest,
    ChangeSetReviewRequest,
    DeliveryFinalizeRequest,
    DeliveryRegenerateRequest,
    RemediationRequest,
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
from src.api.services.workspace_registry_service import list_recent_projects

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _workspace_for_run(thread_id: str, *, require_session: bool = False) -> str:
    """Resolve the workspace associated with a run before reading run artifacts."""
    store = get_event_store()

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
        raise_404(f"No change set found for run {thread_id}. Use POST collect first.")
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
    )


@router.post("/{thread_id}/actions/execute")
async def post_action_execute(thread_id: str, request: ActionExecuteRequest):
    """Execute an action through the unified pipeline."""
    from src.api.services.action_execution_service import execute_action
    workspace = _workspace_for_run(thread_id)

    return execute_action(
        kind=request.kind,
        target=request.target,
        payload=request.payload or {},
        thread_id=thread_id,
        workspace_dir=workspace,
    )


@router.get("/{thread_id}/audit")
async def get_audit_trail(thread_id: str):
    """Return the audit trail for a run."""
    from src.api.services.action_execution_service import get_audit_trail
    workspace = _workspace_for_run(thread_id)

    return get_audit_trail(thread_id, workspace)
