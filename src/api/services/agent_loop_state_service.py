"""Persistent Agent Loop state for one run.

The loop state is intentionally a ledger, not a workflow graph.  It records what
the Lead observed and decided at each step so the frontend and tests can explain
why a run answered, inspected, called tools, waited for approval, or stopped.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.agent.decision_protocol import LeadAction
from src.api.models import IntentDecision
from src.api.services.event_store import get_event_store
from src.runtime.tool_policy_runtime import ToolPolicyDecision, classify_tool_permission


class AgentLoopStep(BaseModel):
    id: str
    step_number: int
    phase: str = "act"
    action: LeadAction
    status: str = "completed"
    summary: str = ""
    event_ids: list[str] = Field(default_factory=list)
    started_at: float
    completed_at: float | None = None


class AgentLoopState(BaseModel):
    thread_id: str
    conversation_id: str | None = None
    workspace_dir: str
    user_request: str
    intent: IntentDecision
    current_step: int = 0
    max_steps: int = 40
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    active_agent: str = "Lead"
    context_pack_id: str | None = None
    pending_approval_id: str | None = None
    terminal_status: str | None = None
    steps: list[AgentLoopStep] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class LoopStepLimitExceeded(RuntimeError):
    """Raised when the Lead loop tries to continue after max_steps."""


class LoopActionRejected(RuntimeError):
    """Raised when a Lead action contradicts the current loop contract."""


def init_agent_loop_state(
    thread_id: str,
    workspace_dir: str,
    *,
    user_request: str,
    intent: dict[str, Any] | IntentDecision,
    conversation_id: str | None = None,
    max_steps: int = 40,
) -> AgentLoopState:
    """Create or update the run's loop state with immutable run metadata."""
    existing = load_agent_loop_state(thread_id, workspace_dir)
    intent_model = intent if isinstance(intent, IntentDecision) else IntentDecision.model_validate(intent)
    if existing:
        existing.intent = intent_model
        existing.user_request = user_request or existing.user_request
        existing.conversation_id = conversation_id or existing.conversation_id
        existing.max_steps = max_steps or existing.max_steps
        existing.updated_at = time.time()
        _save(existing)
        return existing

    state = AgentLoopState(
        thread_id=thread_id,
        workspace_dir=str(Path(workspace_dir).resolve()),
        conversation_id=conversation_id,
        user_request=user_request,
        intent=intent_model,
        max_steps=max_steps,
    )
    _save(state)
    return state


def load_agent_loop_state(thread_id: str, workspace_dir: str) -> AgentLoopState | None:
    path = _state_path(thread_id, workspace_dir)
    if not path.exists():
        return None
    try:
        return AgentLoopState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_agent_loop_state(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Return a loop state, deriving a minimal one from session metadata if needed."""
    state = load_agent_loop_state(thread_id, workspace_dir)
    if not state:
        session = get_event_store().get_session(thread_id, workspace_dir) or {}
        intent = session.get("intent_decision") if isinstance(session.get("intent_decision"), dict) else None
        if not intent:
            plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
            intent = plan.get("intent_decision") if isinstance(plan.get("intent_decision"), dict) else None
        if not intent:
            from src.api.services.intent_router import classify_user_intent

            intent = classify_user_intent(str(session.get("prompt") or ""))
        state = init_agent_loop_state(
            thread_id,
            workspace_dir,
            user_request=str(session.get("prompt") or ""),
            intent=intent,
            conversation_id=session.get("conversation_id"),
        )
    data = state.model_dump()
    readiness = assess_loop_finish_readiness(thread_id, workspace_dir, state=state)
    data["finish_readiness"] = readiness
    data["next_actions"] = suggest_loop_next_actions(state, readiness)
    return data


def _load_state_for_guard(thread_id: str, workspace_dir: str) -> AgentLoopState | None:
    state = load_agent_loop_state(thread_id, workspace_dir)
    if state:
        return state
    session = get_event_store().get_session(thread_id, workspace_dir) or {}
    intent = session.get("intent_decision") if isinstance(session.get("intent_decision"), dict) else None
    if not intent:
        plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
        intent = plan.get("intent_decision") if isinstance(plan.get("intent_decision"), dict) else None
    if not intent:
        return None
    return init_agent_loop_state(
        thread_id,
        workspace_dir,
        user_request=str(session.get("prompt") or ""),
        intent=intent,
        conversation_id=session.get("conversation_id"),
    )


def append_loop_step(
    thread_id: str,
    workspace_dir: str,
    *,
    action: dict[str, Any] | LeadAction,
    phase: str = "act",
    status: str = "completed",
    summary: str = "",
    event_id: str | None = None,
    context_pack_id: str | None = None,
    pending_approval_id: str | None = None,
) -> AgentLoopState:
    """Append one validated loop step and persist the updated state."""
    state_dict = get_agent_loop_state(thread_id, workspace_dir)
    state = AgentLoopState.model_validate(state_dict)
    action_model = action if isinstance(action, LeadAction) else LeadAction.model_validate(action)
    if (
        state.current_step >= state.max_steps
        and action_model.type not in {"finish", "fail"}
    ):
        if state.terminal_status not in {"failed", "completed", "cancelled"}:
            state = _mark_step_limit_exceeded(state)
            _save(state)
        raise LoopStepLimitExceeded(f"Lead loop exceeded max_steps={state.max_steps}.")
    gate = validate_loop_action(state, action_model)
    if not gate["allowed"]:
        _record_loop_action_rejection(state, action_model, gate)
        raise LoopActionRejected(str(gate["reason"]))
    now = time.time()
    readiness: dict[str, Any] | None = None
    step_summary = summary or action_model.goal or action_model.final_message[:240]
    if action_model.type == "finish":
        readiness = assess_loop_finish_readiness(thread_id, workspace_dir, state=state)
        action_model.context_requirements.setdefault("finish_readiness", readiness)
        if not readiness.get("ready") and "Finish readiness warning:" not in step_summary:
            step_summary = f"{step_summary} Finish readiness warning: {readiness.get('reason')}"
    step = AgentLoopStep(
        id=f"step-{state.current_step + 1:03d}-{uuid.uuid4().hex[:8]}",
        step_number=state.current_step + 1,
        phase=phase,
        action=action_model,
        status=status,
        summary=step_summary,
        event_ids=[event_id] if event_id else [],
        started_at=now,
        completed_at=now if status in {"completed", "failed", "waiting"} else None,
    )
    state.steps.append(step)
    state.current_step = step.step_number
    state.active_agent = action_model.agent or state.active_agent
    if context_pack_id:
        state.context_pack_id = context_pack_id
    if pending_approval_id:
        state.pending_approval_id = pending_approval_id
    if action_model.approval and not state.pending_approval_id:
        state.pending_approval_id = str(action_model.approval.get("approval_id") or "")
    if action_model.type == "finish":
        state.terminal_status = "completed"
    elif action_model.type == "fail":
        state.terminal_status = "failed"
    elif status == "waiting":
        state.terminal_status = "waiting_approval"
    state.updated_at = now
    _save(state)
    return state


def check_loop_action(
    thread_id: str,
    workspace_dir: str,
    action: dict[str, Any] | LeadAction,
) -> dict[str, Any]:
    """Dry-run one Lead action without mutating the loop ledger."""
    state = AgentLoopState.model_validate(get_agent_loop_state(thread_id, workspace_dir))
    try:
        action_model = action if isinstance(action, LeadAction) else LeadAction.model_validate(action)
    except ValidationError as exc:
        return {
            "allowed": False,
            "reason": "Lead action schema validation failed.",
            "code": "invalid_action_schema",
            "schema_errors": exc.errors(),
            "action": action if isinstance(action, dict) else action.model_dump(),
            "repaired_action": None,
            "finish_readiness": assess_loop_finish_readiness(thread_id, workspace_dir, state=state),
            "next_actions": suggest_loop_next_actions(state),
        }

    if state.current_step >= state.max_steps and action_model.type not in {"finish", "fail"}:
        gate = _loop_gate_decision(
            False,
            f"Lead loop exceeded max_steps={state.max_steps}.",
            "loop_step_limit",
            suggested_repair="fail",
        )
    else:
        gate = validate_loop_action(state, action_model)
    readiness = assess_loop_finish_readiness(thread_id, workspace_dir, state=state)
    return {
        "allowed": bool(gate["allowed"]),
        "reason": gate["reason"],
        "code": gate["code"],
        "suggested_repair": gate.get("suggested_repair", ""),
        "action": action_model.model_dump(),
        "repaired_action": suggest_loop_action_repair(state, action_model, gate),
        "finish_readiness": readiness,
        "next_actions": suggest_loop_next_actions(state, readiness),
    }


def validate_loop_action(state: AgentLoopState, action: LeadAction) -> dict[str, Any]:
    """Validate one Lead action against the current loop contract.

    Tool governance still validates concrete tool calls.  This gate validates
    the higher-level loop action so the ledger cannot drift into a contradictory
    story, such as a direct-answer run spawning implementation tasks.
    """
    if state.terminal_status in {"completed", "failed", "cancelled"}:
        return _loop_gate_decision(
            False,
            f"Lead loop 已终止: {state.terminal_status}",
            "loop_terminal",
            suggested_repair="start_new_run",
        )

    action_type = action.type
    if state.intent.execution_route == "lead_direct_reply":
        allowed = {"answer", "ask_clarification", "finish", "fail"}
        if action_type not in allowed:
            return _loop_gate_decision(
                False,
                f"lead_direct_reply 只能直接回答或结束，不能执行 {action_type}。",
                "direct_answer_action_mismatch",
                suggested_repair="answer",
            )

    if state.intent.route == "clarification_needed":
        allowed = {"ask_clarification", "answer", "finish", "fail"}
        if action_type not in allowed:
            return _loop_gate_decision(
                False,
                f"clarification_needed 缺少必要信息，不能执行 {action_type}。",
                "clarification_action_mismatch",
                suggested_repair="ask_clarification",
            )

    if state.intent.route == "small_edit":
        allowed = {
            "inspect_project",
            "call_tool",
            "request_approval",
            "run_checks",
            "summarize",
            "merge_agent_result",
            "finish",
            "fail",
        }
        if action_type not in allowed:
            return _loop_gate_decision(
                False,
                f"small_edit 只允许受控局部修改动作，不能执行 {action_type}。",
                "small_edit_action_mismatch",
                suggested_repair="inspect_project",
            )

    if action_type == "merge_agent_result":
        requirements = action.context_requirements if isinstance(action.context_requirements, dict) else {}
        agent_id = str(requirements.get("agent_id") or requirements.get("source_agent_id") or "")
        evidence_pack_id = str(requirements.get("evidence_pack_id") or "")
        if not agent_id or not evidence_pack_id:
            return _loop_gate_decision(
                False,
                "merge_agent_result action 必须包含 agent_id 和 evidence_pack_id。",
                "merge_agent_result_payload_missing",
                suggested_repair="summarize",
            )

    if state.intent.route in {"read_only", "review_only"} and action_type in {"run_checks"}:
        return _loop_gate_decision(
            False,
            f"{state.intent.route} 默认不运行检查命令，除非路由升级为 test_only 或写入任务。",
            "read_only_run_checks",
            suggested_repair="inspect_project",
        )

    if action_type == "call_tool" and action.tool_call:
        tool_name = str(action.tool_call.get("tool") or action.tool_call.get("kind") or "")
        tool_input = action.tool_call.get("input") if isinstance(action.tool_call.get("input"), dict) else {}
        permission = classify_tool_permission(tool_name, tool_input)
        if state.intent.route in {"read_only", "review_only"} and permission in {
            "safe_write",
            "risky_write",
            "mcp_write",
            "shell_risky",
            "external_risky",
        }:
            return _loop_gate_decision(
                False,
                f"{state.intent.route} 不能提交 {permission} 工具动作。",
                "read_only_tool_action_mismatch",
                suggested_repair="inspect_project",
            )
        if state.intent.route == "small_edit" and permission in {
            "risky_write",
            "shell_risky",
            "external_risky",
            "mcp_write",
        }:
            return _loop_gate_decision(
                False,
                f"small_edit 不能提交 {permission} 工具动作，应升级为更高风险路由。",
                "small_edit_tool_action_mismatch",
                suggested_repair="inspect_project",
            )

    if action_type == "request_approval" and not action.approval:
        return _loop_gate_decision(
            False,
            "request_approval action 必须包含 approval 元数据。",
            "approval_payload_missing",
            suggested_repair="attach_approval",
        )

    if action_type == "call_tool" and not action.tool_call:
        return _loop_gate_decision(
            False,
            "call_tool action 必须包含 tool_call 元数据。",
            "tool_call_payload_missing",
            suggested_repair="attach_tool_call",
        )

    return _loop_gate_decision(True, "allowed", "allowed")


def suggest_loop_action_repair(
    state: AgentLoopState,
    action: LeadAction,
    gate: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a conservative replacement action when the repair is unambiguous."""
    if gate.get("allowed"):
        return None
    code = gate.get("code")
    if code == "direct_answer_action_mismatch":
        return LeadAction(
            type="answer",
            goal=action.goal or "Answer directly without creating tasks.",
            agent="Lead",
            final_message="",
        ).model_dump()
    if code == "clarification_action_mismatch":
        return LeadAction(
            type="ask_clarification",
            goal="Ask for the missing scope or acceptance criteria before executing.",
            agent="Lead",
            final_message="请补充要处理的对象、范围和验收标准。",
        ).model_dump()
    if code == "read_only_run_checks":
        return LeadAction(
            type="inspect_project",
            goal=action.goal or "Inspect project context without running commands.",
            agent="Lead",
            task_id=action.task_id,
        ).model_dump()
    if code == "read_only_tool_action_mismatch":
        return LeadAction(
            type="inspect_project",
            goal="Inspect project context without write or risky tool actions.",
            agent="Lead",
            task_id=action.task_id,
        ).model_dump()
    if code in {"small_edit_action_mismatch", "small_edit_tool_action_mismatch"}:
        return LeadAction(
            type="inspect_project",
            goal="Inspect project context before a controlled local edit.",
            agent="Lead",
            task_id=action.task_id,
        ).model_dump()
    if code == "merge_agent_result_payload_missing":
        return LeadAction(
            type="summarize",
            goal="Summarize child Agent output only after agent_id and evidence_pack_id are available.",
            agent="Lead",
            task_id=action.task_id,
        ).model_dump()
    if code == "loop_step_limit":
        return LeadAction(
            type="fail",
            goal=f"Stop because max_steps={state.max_steps} was reached.",
            agent="Lead",
            final_message="Lead loop reached max_steps and stopped.",
        ).model_dump()
    return None


def _loop_gate_decision(
    allowed: bool,
    reason: str,
    code: str,
    *,
    suggested_repair: str = "",
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "code": code,
        "suggested_repair": suggested_repair,
    }


def _record_loop_action_rejection(
    state: AgentLoopState,
    action: LeadAction,
    gate: dict[str, Any],
) -> None:
    try:
        get_event_store().append_event(
            state.thread_id,
            "agent_loop_action_rejected",
            title="Agent Loop 动作被拒绝",
            content=str(gate.get("reason") or ""),
            agent="lead",
            payload={
                "action": action.model_dump(),
                "gate": gate,
                "route": state.intent.route,
                "execution_route": state.intent.execution_route,
                "current_step": state.current_step,
                "terminal_status": state.terminal_status,
            },
            workspace_dir=state.workspace_dir,
        )
    except Exception:
        pass


def assess_loop_finish_readiness(
    thread_id: str,
    workspace_dir: str,
    *,
    state: AgentLoopState | None = None,
) -> dict[str, Any]:
    """Inspect whether the Lead loop has enough evidence to finish successfully."""
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness

    if state is None:
        state = load_agent_loop_state(thread_id, workspace_dir)
    return build_loop_finish_readiness(thread_id, workspace_dir, state=state)


def suggest_loop_next_actions(state: AgentLoopState, readiness: dict[str, Any] | None = None) -> list[str]:
    """Return a small action menu that explains what the Lead loop should do next."""
    if state.terminal_status in {"completed", "failed", "cancelled"}:
        return []
    readiness = readiness or assess_loop_finish_readiness(state.thread_id, state.workspace_dir, state=state)
    if state.intent.execution_route == "lead_direct_reply":
        return ["answer", "finish"]
    if readiness.get("ready"):
        return ["summarize", "finish"]

    counts = readiness.get("counts") if isinstance(readiness.get("counts"), dict) else {}
    if counts.get("running"):
        return ["observe", "wait_for_tool", "verify"]
    if counts.get("failed") or counts.get("blocked") or counts.get("cancelled"):
        return ["classify_failure", "recover_or_retry", "request_approval", "fail"]
    if counts.get("pending") or counts.get("ready"):
        return ["inspect_project", "call_tool", "run_checks", "summarize"]
    return ["observe", "decide"]


def check_loop_can_continue(thread_id: str, workspace_dir: str) -> ToolPolicyDecision | None:
    """Return a blocking decision if the loop reached its step limit."""
    state = _load_state_for_guard(thread_id, workspace_dir)
    if not state:
        return None
    if state.terminal_status in {"failed", "completed", "cancelled"}:
        return ToolPolicyDecision(
            tool="agent_loop",
            allowed=False,
            reason=f"Lead loop 已终止: {state.terminal_status}",
            permission_level="loop_terminal",
            risk_level="high",
        )
    if state.current_step >= state.max_steps:
        state = _mark_step_limit_exceeded(state)
        _save(state)
        return ToolPolicyDecision(
            tool="agent_loop",
            allowed=False,
            reason=f"Lead loop 达到 max_steps={state.max_steps}，已熔断。",
            permission_level="loop_step_limit",
            risk_level="high",
        )
    return None


def check_loop_tool_guard(
    thread_id: str,
    workspace_dir: str,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> ToolPolicyDecision | None:
    """Block tool calls that contradict the current intent route."""
    limit_decision = check_loop_can_continue(thread_id, workspace_dir)
    if limit_decision:
        limit_decision.tool = tool_name or limit_decision.tool
        return limit_decision

    state = _load_state_for_guard(thread_id, workspace_dir)
    if not state:
        return None
    permission = classify_tool_permission(tool_name, tool_input)
    route = state.intent.route
    execution_route = state.intent.execution_route

    blocked_reason = ""
    if execution_route == "lead_direct_reply" and permission != "read_only":
        blocked_reason = f"{route} 只允许 Lead 直接回答，禁止调用 {permission} 工具。"
    elif route in {"read_only", "review_only", "test_only"} and permission in {"safe_write", "risky_write"}:
        blocked_reason = f"{route} 是非写入任务，禁止写入类工具。"
    elif route in {"read_only", "review_only"} and permission in {"shell_risky", "external_risky"}:
        blocked_reason = f"{route} 不允许高风险 shell 或外部工具。"
    elif route == "test_only" and permission == "shell_risky":
        blocked_reason = "test_only 只允许安全测试/检查命令，禁止高风险 shell。"
    elif route == "small_edit" and permission in {"risky_write", "shell_risky", "external_risky", "mcp_write"}:
        blocked_reason = f"small_edit 只允许受控局部修改，禁止 {permission} 工具。"

    if not blocked_reason:
        return None

    return ToolPolicyDecision(
        tool=tool_name,
        allowed=False,
        reason=blocked_reason,
        permission_level=permission,
        risk_level="high",
    )


def check_loop_action_guard(
    thread_id: str,
    workspace_dir: str,
    *,
    kind: str,
    target: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Block action API calls that contradict the current intent route."""
    limit_decision = check_loop_can_continue(thread_id, workspace_dir)
    if limit_decision:
        return {
            "allowed": False,
            "requires_approval": False,
            "reason": limit_decision.reason,
            "risk": limit_decision.risk_level,
            "permission_level": limit_decision.permission_level,
        }

    state = _load_state_for_guard(thread_id, workspace_dir)
    if not state:
        return None
    try:
        from src.runtime.action_policy import ActionKind, classify_action_permission

        permission = classify_action_permission(ActionKind(kind), target, payload=payload)
    except Exception:
        permission = classify_tool_permission(_tool_name_for_action(kind, target), {"command": target, "path": target})

    route = state.intent.route
    execution_route = state.intent.execution_route
    blocked_reason = ""
    recovery_context = payload.get("recovery_context") if isinstance(payload, dict) else None
    if (
        isinstance(recovery_context, dict)
        and recovery_context.get("mode") == "failure_recovery"
        and permission in {"read_only", "shell_safe"}
    ):
        return None
    if execution_route == "lead_direct_reply" and permission not in {"read_only", "mcp_read"}:
        blocked_reason = f"{route} 只允许 Lead 直接回答或只读动作，禁止调用 {permission} 工具。"
    elif route in {"read_only", "review_only", "test_only"} and permission in {"safe_write", "risky_write", "mcp_write"}:
        blocked_reason = f"{route} 是非写入任务，禁止写入类工具。"
    elif route in {"read_only", "review_only"} and permission in {"shell_risky", "external_risky"}:
        blocked_reason = f"{route} 不允许高风险 shell 或外部工具。"
    elif route == "test_only" and permission == "shell_risky":
        blocked_reason = "test_only 只允许安全测试/检查命令，禁止高风险 shell。"
    elif route == "small_edit" and permission in {"risky_write", "shell_risky", "external_risky", "mcp_write"}:
        blocked_reason = f"small_edit 只允许受控局部修改，禁止 {permission} 工具。"

    if not blocked_reason:
        return None

    return {
        "allowed": False,
        "requires_approval": False,
        "reason": blocked_reason,
        "risk": "high",
        "permission_level": permission,
    }


def finalize_agent_loop_state(
    thread_id: str,
    workspace_dir: str,
    *,
    status: str,
    final_message: str = "",
) -> AgentLoopState:
    existing = load_agent_loop_state(thread_id, workspace_dir)
    if existing and existing.terminal_status:
        return existing
    action_type = "finish" if status == "completed" else "fail"
    readiness = assess_loop_finish_readiness(thread_id, workspace_dir)
    summary = final_message[:500] if final_message else f"Run finalized as {status}."
    if status == "completed" and not readiness.get("ready"):
        summary = f"{summary} Finish readiness warning: {readiness.get('reason')}"
    state = append_loop_step(
        thread_id,
        workspace_dir,
        action={
            "type": action_type,
            "goal": f"Finalize run as {status}.",
            "agent": "Lead",
            "context_requirements": {"finish_readiness": readiness},
            "final_message": final_message,
        },
        phase="final",
        status="completed" if status == "completed" else "failed",
        summary=summary,
    )
    state.terminal_status = status
    state.updated_at = time.time()
    _save(state)
    return state


def _mark_step_limit_exceeded(state: AgentLoopState) -> AgentLoopState:
    if state.terminal_status == "failed":
        return state
    now = time.time()
    step = AgentLoopStep(
        id=f"step-{state.current_step + 1:03d}-{uuid.uuid4().hex[:8]}",
        step_number=state.current_step + 1,
        phase="final",
        action=LeadAction(
            type="fail",
            goal=f"Stop because max_steps={state.max_steps} was reached.",
            agent="Lead",
            final_message="Lead loop reached max_steps and stopped.",
        ),
        status="failed",
        summary=f"Lead loop reached max_steps={state.max_steps}.",
        started_at=now,
        completed_at=now,
    )
    state.steps.append(step)
    state.current_step = step.step_number
    state.terminal_status = "failed"
    state.updated_at = now
    return state


def _tool_name_for_action(kind: str, target: str = "") -> str:
    if kind == "run_command":
        return "bash"
    if kind == "git_operation":
        return "bash"
    if kind == "recovery_action":
        return "restore_snapshot"
    if kind == "mcp_call":
        return "mcp_call"
    return kind or target


def _state_path(thread_id: str, workspace_dir: str) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir) / "loop_state.json"


def _save(state: AgentLoopState) -> None:
    path = _state_path(state.thread_id, state.workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(state.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
