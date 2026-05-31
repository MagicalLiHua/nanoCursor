"""Tool approval routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.models import ApprovalDecisionRequest, ToolApprovalResolveRequest
from src.api.run_state import emit_agenthub_event, workspace_for_thread
from src.api.services.approval_service import (
    get_pending_approvals,
    get_tool_approval,
    resolve_tool_approval,
)

router = APIRouter(tags=["approvals"])


def _approval_title(decision: str) -> str:
    labels = {
        "approved": "计划已批准",
        "revise": "计划需调整",
        "rejected": "计划已拒绝",
    }
    return labels.get(decision, "计划审批已记录")


@router.post("/api/runs/{thread_id}/approval")
async def resolve_approval(thread_id: str, decision: ApprovalDecisionRequest):
    title = _approval_title(decision.decision)
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="plan_approved" if decision.decision == "approved" else "approval_resolved",
        title=title,
        content=decision.feedback or "",
        agent="lead",
        payload={
            "plan_id": decision.plan_id,
            "decision": decision.decision,
            "feedback": decision.feedback,
        },
        workspace_dir=workspace_for_thread(thread_id),
    )
    return {"thread_id": thread_id, "plan_id": decision.plan_id, "decision": decision.decision}


@router.get("/api/runs/{thread_id}/approvals")
async def list_run_approvals(thread_id: str):
    ws = workspace_for_thread(thread_id)
    return {"approvals": get_pending_approvals(thread_id, ws)}


@router.get("/api/runs/{thread_id}/approvals/{decision_id}")
async def get_run_approval(thread_id: str, decision_id: str):
    ws = workspace_for_thread(thread_id)
    result = get_tool_approval(thread_id, decision_id, ws)
    if not result:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    return result


@router.post("/api/runs/{thread_id}/approvals/{decision_id}")
async def resolve_run_approval(thread_id: str, decision_id: str, body: ToolApprovalResolveRequest):
    ws = workspace_for_thread(thread_id)
    result = resolve_tool_approval(thread_id, decision_id, body.approved, body.comment, ws)
    if not result:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    return result
