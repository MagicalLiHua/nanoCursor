"""Approval wait orchestration for streamed runtime tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.api.services.agent_loop_state_service import append_loop_step
from src.api.services.approval_service import (
    create_tool_approval,
    wait_for_approval_async,
)
from src.runtime.run_state import RunStatus


EmitEvent = Callable[..., Any]
EmitActivity = Callable[..., Any]
TransitionState = Callable[[str, str, RunStatus], None]
ShouldCancel = Callable[[str], bool]


@dataclass(frozen=True)
class RuntimeApprovalWaitContext:
    thread_id: str
    workspace_dir: str
    emit_event: EmitEvent
    emit_activity: EmitActivity
    transition_state: TransitionState
    should_cancel: ShouldCancel
    timeout_seconds: float = 120.0


async def resolve_runtime_tool_approval(
    *,
    context: RuntimeApprovalWaitContext,
    tool_name: str,
    tool_input: dict[str, Any],
    decision: Any,
) -> dict[str, Any]:
    """Request, wait for, and record approval for one high-risk tool call.

    The caller owns the mutable decision object. This helper mutates it in the
    same way as the previous inline implementation so runtime behavior stays
    stable while approval waiting has a focused boundary.
    """
    create_tool_approval(
        context.thread_id,
        decision,
        context.workspace_dir,
        timeout_seconds=context.timeout_seconds,
    )
    _record_approval_wait_step(context, tool_name, tool_input, decision)
    context.transition_state(context.thread_id, context.workspace_dir, RunStatus.WAITING_APPROVAL)
    _emit_approval_requested(context, tool_name, decision)

    resolved = await wait_for_approval_async(
        context.thread_id,
        decision,
        timeout_seconds=context.timeout_seconds,
        workspace_dir=context.workspace_dir,
        should_abort=lambda: context.should_cancel(context.thread_id),
    )
    if resolved.get("status") == "approved":
        decision.allowed = True
        decision.status = "approved"
    else:
        decision.allowed = False
        decision.status = "rejected"
        decision.reason = resolved.get("reason", "用户拒绝执行该工具。")
    context.transition_state(context.thread_id, context.workspace_dir, RunStatus.RUNNING)
    context.emit_event(
        thread_id=context.thread_id,
        event_type="approval_resolved",
        title=f"审批结果: {decision.status}",
        content=resolved.get("comment", ""),
        agent="system",
        payload={"tool": tool_name, "decision": decision.to_dict(), "resolved": resolved},
        workspace_dir=context.workspace_dir,
    )
    return resolved


def _record_approval_wait_step(
    context: RuntimeApprovalWaitContext,
    tool_name: str,
    tool_input: dict[str, Any],
    decision: Any,
) -> None:
    try:
        append_loop_step(
            context.thread_id,
            context.workspace_dir,
            phase="act",
            status="waiting",
            action={
                "type": "request_approval",
                "goal": decision.reason,
                "agent": "Lead",
                "tool_call": {"tool": tool_name, "input": tool_input},
                "approval": {
                    "tool": tool_name,
                    "timeout_seconds": context.timeout_seconds,
                },
            },
            summary=f"Waiting for approval: {tool_name}",
            pending_approval_id=tool_name,
        )
    except Exception:
        pass


def _emit_approval_requested(
    context: RuntimeApprovalWaitContext,
    tool_name: str,
    decision: Any,
) -> None:
    context.emit_event(
        thread_id=context.thread_id,
        event_type="tool_approval_required",
        title=f"工具需要审批: {tool_name}",
        content=decision.reason,
        agent="system",
        payload={"tool": tool_name, "decision": decision.to_dict()},
        workspace_dir=context.workspace_dir,
    )
    context.emit_activity(
        thread_id=context.thread_id,
        agent="system",
        title="等待用户批准高风险工具",
        content=decision.reason,
        workspace_dir=context.workspace_dir,
        payload={
            "phase": "approval_wait",
            "tool": tool_name,
            "decision": decision.to_dict(),
            "can_cancel": True,
        },
    )
    context.emit_event(
        thread_id=context.thread_id,
        event_type="run_waiting_approval",
        title="等待用户审批",
        content=f"等待审批工具: {tool_name}",
        agent="system",
        payload={
            "tool": tool_name,
            "decision": decision.to_dict(),
            "timeout_seconds": context.timeout_seconds,
        },
        workspace_dir=context.workspace_dir,
    )
