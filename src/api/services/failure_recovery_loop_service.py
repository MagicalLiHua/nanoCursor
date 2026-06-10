"""Failure recovery loop planning.

This module is intentionally conservative: it builds evidence and a reviewable
plan first. Actual edits, installs, deletes, or shell reruns stay outside this
service until a caller explicitly executes an approved step.
"""

from __future__ import annotations

import json
import inspect
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from src.api.models import AgentEvent
from src.api.services.event_store import get_event_store
from src.api.services.failure_classifier_service import (
    FailureClass,
    FailureRecord,
    SuggestedAction,
)
from src.infra.logging import get_logger


logger = get_logger()

RecoveryStepKind = Literal[
    "inspect_file",
    "edit_file",
    "rerun_command",
    "fallback_tool",
    "ask_approval",
    "ask_user",
    "stop",
]


class CommandFailureEvidence(BaseModel):
    """Normalized evidence extracted from a failed command or tool event."""

    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")
    thread_id: str
    workspace_dir: str
    event_id: str | None = None
    event_type: str | None = None
    agent: str = "system"
    tool_name: str | None = None
    command: str | None = None
    cwd: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    message: str = ""
    related_files: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    raw_excerpt: dict[str, Any] = Field(default_factory=dict)


class RecoveryPlanStep(BaseModel):
    """One bounded recovery step. The executor can later map it to tools."""

    step_id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    kind: RecoveryStepKind
    title: str
    reason: str
    agent: str = "lead"
    target: str | None = None
    command: str | None = None
    requires_approval: bool = False
    risk: Literal["low", "medium", "high"] = "low"
    status: Literal["planned", "skipped", "done", "failed"] = "planned"


class RecoveryPlan(BaseModel):
    """Reviewable plan for recovering from one failure."""

    plan_id: str = Field(default_factory=lambda: f"rplan_{uuid.uuid4().hex[:12]}")
    thread_id: str
    workspace_dir: str
    failure_id: str
    evidence_id: str
    source_task_id: str = ""
    failure_type: str
    failure_class: FailureClass
    title: str
    summary: str
    confidence: Literal["low", "medium", "high"] = "medium"
    can_auto_recover: bool = False
    retry_budget: int = 1
    steps: list[RecoveryPlanStep] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class RecoveryStepExecution(BaseModel):
    """Execution result for one planned recovery step."""

    step_id: str
    kind: RecoveryStepKind
    status: Literal["succeeded", "failed", "skipped", "waiting_approval", "waiting_agent", "stopped"]
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None


class RecoveryAttempt(BaseModel):
    """One attempt to execute a recovery plan."""

    attempt_id: str = Field(default_factory=lambda: f"rattempt_{uuid.uuid4().hex[:12]}")
    thread_id: str
    workspace_dir: str
    plan_id: str
    failure_id: str
    status: Literal["running", "succeeded", "failed", "waiting_approval", "waiting_agent", "stopped"]
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None
    retry_budget_remaining: int = 0
    step_results: list[RecoveryStepExecution] = Field(default_factory=list)
    stop_reason: str = ""


class RecoveryAgentTaskRun(BaseModel):
    """Result of running a Coder recovery task package."""

    run_id: str = Field(default_factory=lambda: f"repairrun_{uuid.uuid4().hex[:12]}")
    thread_id: str
    workspace_dir: str
    task_id: str
    plan_id: str
    failure_id: str
    package_id: str
    status: Literal["passed", "failed", "blocked"]
    summary: str = ""
    output: str = ""
    error: str = ""
    validation_command: str = ""
    validation_result: dict[str, Any] = Field(default_factory=dict)
    started_at: float = Field(default_factory=time.time)
    completed_at: float | None = None
    duration_ms: int = 0
    next_action: dict[str, Any] | None = None


RecoveryAgentRunner = Callable[[str, str, str, list[dict[str, Any]]], Any]


STATE_FILE = "recovery_loop.json"
_TAIL_LIMIT = 4000


def get_recovery_loop_state(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Load the persisted recovery-loop state for a run."""

    path = _state_path(thread_id, workspace_dir)
    if not path.exists():
        return _empty_state(thread_id, workspace_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("recovery_loop_state_load_failed", extra={"thread_id": thread_id}, exc_info=True)
        return _empty_state(thread_id, workspace_dir)
    if not isinstance(data, dict):
        return _empty_state(thread_id, workspace_dir)
    return {**_empty_state(thread_id, workspace_dir), **data}


def get_recovery_plan(
    thread_id: str,
    failure_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any] | None:
    """Return the latest plan for a failure id, if present."""

    state = get_recovery_loop_state(thread_id, workspace_dir)
    for plan in reversed(state.get("plans", [])):
        if isinstance(plan, dict) and plan.get("failure_id") == failure_id:
            return plan
    return None


def prepare_recovery_agent_task(
    thread_id: str,
    workspace_dir: str | None = None,
    *,
    task_id: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Prepare a Coder-facing package for the latest recovery repair task."""

    state = get_recovery_loop_state(thread_id, workspace_dir)
    plan_data = _select_plan(state, plan_id)
    if not plan_data:
        raise ValueError("未找到可用于准备 Agent 修复任务的恢复计划。")
    plan = RecoveryPlan(**plan_data)
    selected_task_id = task_id or _latest_recovery_task_id(thread_id, workspace_dir, plan)
    if not selected_task_id:
        raise ValueError("未找到可消费的 recovery task。")
    package = _prepare_agent_repair_task(thread_id, workspace_dir, plan, selected_task_id)
    return package


async def execute_recovery_agent_task_async(
    thread_id: str,
    workspace_dir: str | None = None,
    *,
    task_id: str | None = None,
    plan_id: str | None = None,
    auto_validate: bool = True,
    runner: RecoveryAgentRunner | None = None,
) -> dict[str, Any]:
    """Run the prepared Coder recovery task and persist its task-board result.

    The runner is injectable so tests and future Go/Python execution backends
    can share the same task package contract without calling a real LLM.
    """

    package = prepare_recovery_agent_task(thread_id, workspace_dir, task_id=task_id, plan_id=plan_id)
    workspace = str(_workspace(workspace_dir))
    started_at = time.time()
    task_id = str(package["task_id"])
    _emit_agent_task_event(
        thread_id,
        workspace,
        "failure_recovery_agent_task_started",
        "Coder 修复任务开始执行",
        package,
    )
    try:
        output = await _run_recovery_agent_package(package, runner)
        status, summary, error = _classify_recovery_agent_output(output)
    except Exception as exc:
        output = ""
        status = "failed"
        summary = "Coder recovery task failed before producing a repair summary."
        error = str(exc)

    completed_at = time.time()
    run = RecoveryAgentTaskRun(
        thread_id=thread_id,
        workspace_dir=workspace,
        task_id=task_id,
        plan_id=str(package["plan_id"]),
        failure_id=str(package["failure_id"]),
        package_id=str(package["package_id"]),
        status=status,
        summary=summary,
        output=output,
        error=error,
        validation_command=str(package.get("validation_command") or ""),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=int((completed_at - started_at) * 1000),
        next_action=_next_action_after_agent_task(status, package),
    )
    if auto_validate and run.status == "passed" and run.validation_command:
        run = await _validate_recovery_agent_run(thread_id, workspace, package, run)
    task_result = _complete_recovery_agent_task(thread_id, workspace, package, run)
    _persist_recovery_agent_run(thread_id, workspace, task_id, run)
    _emit_agent_task_event(
        thread_id,
        workspace,
        "failure_recovery_agent_task_completed" if status == "passed" else "failure_recovery_agent_task_failed",
        summary or error or "Coder 修复任务结束",
        package,
        extra={
            "run": run.model_dump(mode="json"),
            "task_result": task_result,
        },
    )
    return {
        "thread_id": thread_id,
        "workspace_dir": workspace,
        "package": package,
        "run": run.model_dump(mode="json"),
        "task_result": task_result,
        "next_action": run.next_action,
    }


async def execute_recovery_plan_async(
    thread_id: str,
    workspace_dir: str | None = None,
    *,
    plan_id: str | None = None,
    auto_execute: bool = True,
) -> dict[str, Any]:
    """Execute the safe subset of a recovery plan.

    Phase 2 deliberately avoids automatic code edits. The executor can inspect
    files, run safe commands through the unified action pipeline, or stop at
    approval/agent-required steps with a durable attempt record.
    """

    state = get_recovery_loop_state(thread_id, workspace_dir)
    plan_data = _select_plan(state, plan_id)
    if not plan_data:
        raise ValueError("未找到可执行的恢复计划。")
    plan = RecoveryPlan(**plan_data)
    if not auto_execute:
        attempt = _new_attempt(thread_id, workspace_dir, plan, status="stopped", stop_reason="auto_execute=false")
        attempt.completed_at = time.time()
        state = _append_attempt(thread_id, workspace_dir, attempt)
        return {"thread_id": thread_id, "attempt": attempt.model_dump(mode="json"), "state": state}

    used_attempts = _attempt_count_for_failure(state, plan.failure_id)
    if used_attempts >= max(plan.retry_budget, 0):
        attempt = _new_attempt(
            thread_id,
            workspace_dir,
            plan,
            status="stopped",
            stop_reason="恢复重试预算已用尽。",
        )
        attempt.completed_at = time.time()
        state = _append_attempt(thread_id, workspace_dir, attempt)
        _emit_recovery_event(thread_id, workspace_dir, "failure_recovery_stopped", attempt.stop_reason, attempt)
        return {"thread_id": thread_id, "attempt": attempt.model_dump(mode="json"), "state": state}

    attempt = _new_attempt(
        thread_id,
        workspace_dir,
        plan,
        status="running",
        stop_reason="",
        retry_budget_remaining=max(plan.retry_budget - used_attempts - 1, 0),
    )
    _emit_recovery_event(thread_id, workspace_dir, "failure_recovery_started", plan.summary, attempt)

    for step in plan.steps:
        step_result = await _execute_plan_step(thread_id, workspace_dir, plan, step)
        attempt.step_results.append(step_result)
        if step_result.status in {"failed", "waiting_approval", "waiting_agent", "skipped"}:
            attempt.status = _attempt_status_from_step(step_result.status)
            attempt.stop_reason = step_result.message
            break

    if attempt.status == "running":
        attempt.status = "succeeded"
        attempt.stop_reason = "恢复计划中的可执行步骤已完成。"
    attempt.completed_at = time.time()
    state = _append_attempt(thread_id, workspace_dir, attempt)

    final_event = {
        "succeeded": "failure_recovery_succeeded",
        "failed": "failure_recovery_failed",
        "waiting_approval": "failure_recovery_waiting_approval",
        "waiting_agent": "failure_recovery_stopped",
        "stopped": "failure_recovery_stopped",
    }.get(attempt.status, "failure_recovery_stopped")
    _emit_recovery_event(thread_id, workspace_dir, final_event, attempt.stop_reason, attempt)
    return {"thread_id": thread_id, "attempt": attempt.model_dump(mode="json"), "state": state}


def stop_recovery_loop(
    thread_id: str,
    workspace_dir: str | None = None,
    *,
    reason: str = "用户停止恢复流程。",
) -> dict[str, Any]:
    """Mark the recovery loop as stopped without executing more steps."""

    state = get_recovery_loop_state(thread_id, workspace_dir)
    state["status"] = "stopped"
    state["stop_reason"] = reason
    state["updated_at"] = time.time()
    _write_json_atomic(_state_path(thread_id, workspace_dir), state)
    get_event_store().append_event(
        thread_id=thread_id,
        event_type="failure_recovery_stopped",
        title="失败恢复已停止",
        content=reason,
        agent="lead",
        payload={"reason": reason},
        workspace_dir=workspace_dir,
    )
    return state


def plan_latest_failure_recovery(
    thread_id: str,
    workspace_dir: str | None = None,
    *,
    event_id: str | None = None,
    tool_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build evidence, classify it, create a recovery plan, and persist state."""

    evidence = build_command_failure_evidence(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        event_id=event_id,
        tool_result=tool_result,
    )
    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(thread_id, workspace_dir, failure, evidence)
    state = _append_plan(thread_id, workspace_dir, evidence, failure, plan)

    store = get_event_store()
    store.append_event(
        thread_id=thread_id,
        event_type="failure_evidence_built",
        title="失败证据已整理",
        content=evidence.message or evidence.stderr_tail or evidence.stdout_tail,
        agent="system",
        payload=evidence.model_dump(mode="json"),
        workspace_dir=workspace_dir,
    )
    store.append_event(
        thread_id=thread_id,
        event_type="failure_recovery_planned",
        title=plan.title,
        content=plan.summary,
        agent="lead",
        payload=plan.model_dump(mode="json"),
        workspace_dir=workspace_dir,
    )
    return {
        "thread_id": thread_id,
        "workspace_dir": str(_workspace(workspace_dir)),
        "evidence": evidence.model_dump(mode="json"),
        "failure": failure.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "state": state,
    }


def build_command_failure_evidence(
    thread_id: str,
    workspace_dir: str | None = None,
    *,
    event_id: str | None = None,
    tool_result: dict[str, Any] | None = None,
) -> CommandFailureEvidence:
    """Extract normalized evidence from a direct tool result or stored event."""

    workspace = _workspace(workspace_dir)
    payload: dict[str, Any] = {}
    event: AgentEvent | None = None
    if tool_result:
        payload = tool_result
    else:
        event = _select_failure_event(thread_id, workspace_dir, event_id)
        if event:
            payload = _event_payload(event)
    if not payload and not event:
        raise ValueError("没有找到可用于恢复计划的失败事件。")

    text_blob = _payload_text(payload, event)
    command = _first_text(payload, ("command", "cmd", "shell_command"))
    if not command:
        command = _nested_text(payload, ("tool_input", "command")) or _nested_text(payload, ("input", "command"))
    stdout = _first_text(payload, ("stdout", "output", "output_tail", "stdout_tail", "result"))
    stderr = _first_text(payload, ("stderr", "error", "error_tail", "stderr_tail", "traceback"))
    if not stderr and event and event.type in {"error", "tool_call_failed", "command_failed"}:
        stderr = event.content or event.title

    exit_code = _first_int(payload, ("exit_code", "returncode", "return_code", "code"))
    timed_out = _first_bool(payload, ("timed_out", "timeout", "is_timeout"))
    related_files = _related_files_from_text(text_blob, workspace)

    return CommandFailureEvidence(
        thread_id=thread_id,
        workspace_dir=str(workspace),
        event_id=event.id if event else _first_text(payload, ("event_id", "id")),
        event_type=event.type if event else _first_text(payload, ("event_type", "type")),
        agent=event.agent if event else (_first_text(payload, ("agent", "role")) or "system"),
        tool_name=_first_text(payload, ("tool_name", "tool", "name")),
        command=command,
        cwd=_first_text(payload, ("cwd", "working_dir", "workdir")),
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        message=_tail(_first_text(payload, ("message", "detail", "title")) or (event.content if event else text_blob)),
        related_files=related_files,
        raw_excerpt=_safe_excerpt(payload),
    )


def classify_command_failure(evidence: CommandFailureEvidence) -> FailureRecord:
    """Classify one normalized failure evidence record."""

    text = " ".join(
        part
        for part in (
            evidence.command or "",
            evidence.message,
            evidence.stderr_tail,
            evidence.stdout_tail,
        )
        if part
    )
    lowered = text.lower()

    failure_type = "unknown_command_failure"
    failure_class = FailureClass.UNKNOWN_ERROR
    confidence: Literal["low", "medium", "high"] = "medium"

    if evidence.timed_out or "timed out" in lowered or "timeout" in lowered:
        failure_type = "timeout"
        failure_class = FailureClass.COMMAND_ERROR
        confidence = "high"
    elif "blocked by policy" in lowered or "requires approval" in lowered or "approval" in lowered:
        failure_type = "tool_policy_blocked"
        failure_class = FailureClass.TOOL_POLICY_BLOCKED
        confidence = "high"
    elif "permission denied" in lowered or "operation not permitted" in lowered:
        failure_type = "permission_denied"
        failure_class = FailureClass.WORKSPACE_ERROR
        confidence = "high"
    elif "module not found" in lowered or "modulenotfounderror" in lowered or "no module named" in lowered:
        failure_type = "module_not_found"
        failure_class = FailureClass.ENVIRONMENT_ERROR
        confidence = "high"
    elif "syntaxerror" in lowered or "indentationerror" in lowered or "nameerror" in lowered:
        failure_type = "python_syntax_error"
        failure_class = FailureClass.PATCH_ERROR
        confidence = "high"
    elif "assertionerror" in lowered or "failed" in lowered or "pytest" in lowered:
        failure_type = "pytest_assertion_failure"
        failure_class = FailureClass.TEST_FAILURE
        confidence = "high"
    elif "command not found" in lowered or "not recognized as" in lowered:
        failure_type = "command_not_found"
        failure_class = FailureClass.ENVIRONMENT_ERROR
        confidence = "high"
    elif "no such file or directory" in lowered:
        failure_type = "path_not_found"
        failure_class = FailureClass.WORKSPACE_ERROR
        confidence = "high"
    elif "connection refused" in lowered or "mcp" in lowered and "unavailable" in lowered:
        failure_type = "mcp_unavailable"
        failure_class = FailureClass.ENVIRONMENT_ERROR
        confidence = "medium"
    elif evidence.exit_code not in (None, 0):
        failure_type = "non_zero_exit"
        failure_class = FailureClass.COMMAND_ERROR
        confidence = "medium"

    actions = _suggest_actions_for_failure_type(failure_type, evidence)
    can_auto_retry = failure_type in {
        "pytest_assertion_failure",
        "python_syntax_error",
        "path_not_found",
        "timeout",
        "non_zero_exit",
    }
    if failure_type in {"tool_policy_blocked", "permission_denied", "command_not_found", "module_not_found"}:
        can_auto_retry = False

    return FailureRecord(
        failure_id=f"failure_{uuid.uuid4().hex[:12]}",
        thread_id=evidence.thread_id,
        failure_class=failure_class,
        title=_failure_title(failure_type),
        summary=_failure_summary(failure_type, evidence),
        stage_id=evidence.raw_excerpt.get("stage_id") if isinstance(evidence.raw_excerpt, dict) else None,
        task_id=evidence.raw_excerpt.get("task_id") if isinstance(evidence.raw_excerpt, dict) else None,
        agent=evidence.agent,
        related_files=evidence.related_files,
        evidence={
            "evidence_id": evidence.evidence_id,
            "event_id": evidence.event_id,
            "event_type": evidence.event_type,
            "tool_name": evidence.tool_name,
            "command": evidence.command,
            "exit_code": evidence.exit_code,
            "timed_out": evidence.timed_out,
            "recovery_failure_type": failure_type,
            "confidence": confidence,
            "stderr_tail": evidence.stderr_tail,
            "stdout_tail": evidence.stdout_tail,
            "related_files": evidence.related_files,
        },
        can_auto_retry=can_auto_retry,
        suggested_actions=actions,
    )


def build_recovery_plan(
    thread_id: str,
    workspace_dir: str | None,
    failure: FailureRecord,
    evidence: CommandFailureEvidence,
) -> RecoveryPlan:
    """Create a bounded recovery plan for a classified failure."""

    failure_type = str(failure.evidence.get("recovery_failure_type") or "unknown_command_failure")
    steps: list[RecoveryPlanStep] = []
    retry_budget = 1
    can_auto_recover = bool(failure.can_auto_retry)
    confidence = str(failure.evidence.get("confidence") or "medium")
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    if failure_type in {"pytest_assertion_failure", "python_syntax_error"}:
        for path in evidence.related_files[:3]:
            steps.append(RecoveryPlanStep(
                kind="inspect_file",
                title=f"检查相关文件 {path}",
                reason="先定位失败文件，避免直接盲改。",
                agent="reviewer" if failure_type == "pytest_assertion_failure" else "coder",
                target=path,
            ))
        steps.append(RecoveryPlanStep(
            kind="edit_file",
            title="最小范围修复代码或测试期望",
            reason="失败属于代码/测试反馈，允许在相关文件内做局部修复。",
            agent="coder",
            target=evidence.related_files[0] if evidence.related_files else None,
            risk="medium",
        ))
        steps.append(_rerun_step(evidence, "重新运行失败命令验证修复"))
        can_auto_recover = True
    elif failure_type == "path_not_found":
        steps.append(RecoveryPlanStep(
            kind="fallback_tool",
            title="重新扫描工作区并定位真实路径",
            reason="路径缺失通常来自上下文过期或相对路径错误。",
            agent="lead",
            target=evidence.cwd,
        ))
        steps.append(_rerun_step(evidence, "使用修正后的路径重试命令"))
        can_auto_recover = True
    elif failure_type == "timeout":
        steps.append(RecoveryPlanStep(
            kind="rerun_command",
            title="缩小范围后重试命令",
            reason="超时先降低执行范围或只运行失败目标，避免无界重试。",
            agent="tester",
            command=evidence.command,
            risk="medium",
        ))
        retry_budget = 1
        can_auto_recover = True
    elif failure_type == "module_not_found":
        steps.append(RecoveryPlanStep(
            kind="inspect_file",
            title="确认缺失模块是本地模块还是外部依赖",
            reason="自动安装依赖属于高风险/环境变更，先区分来源。",
            agent="lead",
            target=evidence.related_files[0] if evidence.related_files else None,
        ))
        steps.append(RecoveryPlanStep(
            kind="ask_user",
            title="需要用户确认依赖处理方式",
            reason="缺失外部依赖时不自动安装；缺失本地模块时再进入代码修复。",
            agent="lead",
            requires_approval=True,
            risk="medium",
        ))
        can_auto_recover = False
    elif failure_type in {"permission_denied", "tool_policy_blocked"}:
        steps.append(RecoveryPlanStep(
            kind="ask_approval",
            title="请求用户确认高风险操作",
            reason="当前失败与权限或策略有关，不能自动绕过。",
            agent="system",
            command=evidence.command,
            requires_approval=True,
            risk="high",
        ))
        can_auto_recover = False
    elif failure_type == "command_not_found":
        steps.append(RecoveryPlanStep(
            kind="stop",
            title="停止自动恢复并提示缺失命令",
            reason="命令不存在通常需要安装工具或切换环境，系统不应自行修改环境。",
            agent="lead",
            command=evidence.command,
            risk="high",
        ))
        can_auto_recover = False
    elif failure_type == "mcp_unavailable":
        steps.append(RecoveryPlanStep(
            kind="fallback_tool",
            title="切换到本地内置工具",
            reason="MCP 不可用时可以优先使用内置文件/索引能力降级。",
            agent="lead",
            target=evidence.tool_name,
        ))
        can_auto_recover = True
    else:
        steps.append(RecoveryPlanStep(
            kind="inspect_file",
            title="整理失败上下文",
            reason="失败类型不明确，先补充证据再决定是否重试。",
            agent="lead",
            target=evidence.related_files[0] if evidence.related_files else None,
        ))
        steps.append(RecoveryPlanStep(
            kind="ask_user",
            title="需要用户确认下一步",
            reason="避免在低置信度失败上自动修改项目。",
            agent="lead",
            requires_approval=True,
            risk="medium",
        ))
        can_auto_recover = False

    return RecoveryPlan(
        thread_id=thread_id,
        workspace_dir=str(_workspace(workspace_dir)),
        failure_id=failure.failure_id,
        evidence_id=evidence.evidence_id,
        source_task_id=_source_task_id_from_failure(failure, evidence),
        failure_type=failure_type,
        failure_class=failure.failure_class,
        title=f"恢复计划：{failure.title}",
        summary=_plan_summary(failure_type, can_auto_recover, steps),
        confidence=confidence,  # type: ignore[arg-type]
        can_auto_recover=can_auto_recover,
        retry_budget=retry_budget,
        steps=steps,
    )


def _state_path(thread_id: str, workspace_dir: str | None) -> Path:
    return get_event_store().run_dir(thread_id, workspace_dir) / STATE_FILE


def _workspace(workspace_dir: str | None) -> Path:
    if workspace_dir:
        return Path(workspace_dir).resolve()
    from src.infra import config as config_module

    return Path(config_module.WORKSPACE_DIR).resolve()


def _empty_state(thread_id: str, workspace_dir: str | None) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "workspace_dir": str(_workspace(workspace_dir)),
        "status": "empty",
        "latest_failure_id": None,
        "latest_plan_id": None,
        "plans": [],
        "attempts": [],
        "summary": {
            "plan_count": 0,
            "auto_recoverable_count": 0,
            "requires_approval_count": 0,
        },
        "updated_at": None,
    }


def _append_plan(
    thread_id: str,
    workspace_dir: str | None,
    evidence: CommandFailureEvidence,
    failure: FailureRecord,
    plan: RecoveryPlan,
) -> dict[str, Any]:
    state = get_recovery_loop_state(thread_id, workspace_dir)
    plan_data = plan.model_dump(mode="json")
    state["status"] = "planned"
    state["latest_failure_id"] = failure.failure_id
    state["latest_plan_id"] = plan.plan_id
    state.setdefault("plans", []).append(plan_data)
    state["last_evidence"] = evidence.model_dump(mode="json")
    plans = [p for p in state.get("plans", []) if isinstance(p, dict)]
    state["summary"] = {
        "plan_count": len(plans),
        "auto_recoverable_count": sum(1 for p in plans if p.get("can_auto_recover")),
        "requires_approval_count": sum(
            1
            for p in plans
            if any(s.get("requires_approval") for s in p.get("steps", []) if isinstance(s, dict))
        ),
    }
    state["updated_at"] = time.time()
    _write_json_atomic(_state_path(thread_id, workspace_dir), state)
    return state


def _source_task_id_from_failure(failure: FailureRecord, evidence: CommandFailureEvidence) -> str:
    """Return the task-board task that originally produced the failure, if known."""

    candidates = [
        getattr(failure, "task_id", None),
        evidence.raw_excerpt.get("task_id") if isinstance(evidence.raw_excerpt, dict) else None,
        failure.evidence.get("task_id") if isinstance(failure.evidence, dict) else None,
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _select_plan(state: dict[str, Any], plan_id: str | None) -> dict[str, Any] | None:
    plans = [plan for plan in state.get("plans", []) if isinstance(plan, dict)]
    if plan_id:
        return next((plan for plan in plans if plan.get("plan_id") == plan_id), None)
    latest = state.get("latest_plan_id")
    if latest:
        found = next((plan for plan in plans if plan.get("plan_id") == latest), None)
        if found:
            return found
    return plans[-1] if plans else None


def _new_attempt(
    thread_id: str,
    workspace_dir: str | None,
    plan: RecoveryPlan,
    *,
    status: Literal["running", "succeeded", "failed", "waiting_approval", "waiting_agent", "stopped"],
    stop_reason: str = "",
    retry_budget_remaining: int = 0,
) -> RecoveryAttempt:
    return RecoveryAttempt(
        thread_id=thread_id,
        workspace_dir=str(_workspace(workspace_dir)),
        plan_id=plan.plan_id,
        failure_id=plan.failure_id,
        status=status,
        retry_budget_remaining=retry_budget_remaining,
        stop_reason=stop_reason,
    )


def _append_attempt(thread_id: str, workspace_dir: str | None, attempt: RecoveryAttempt) -> dict[str, Any]:
    state = get_recovery_loop_state(thread_id, workspace_dir)
    state.setdefault("attempts", []).append(attempt.model_dump(mode="json"))
    state["status"] = attempt.status
    state["latest_attempt_id"] = attempt.attempt_id
    state["updated_at"] = time.time()
    attempts = [item for item in state.get("attempts", []) if isinstance(item, dict)]
    state["summary"] = {
        **(state.get("summary") if isinstance(state.get("summary"), dict) else {}),
        "attempt_count": len(attempts),
        "successful_attempt_count": sum(1 for item in attempts if item.get("status") == "succeeded"),
        "failed_attempt_count": sum(1 for item in attempts if item.get("status") == "failed"),
        "waiting_attempt_count": sum(1 for item in attempts if str(item.get("status", "")).startswith("waiting")),
    }
    _write_json_atomic(_state_path(thread_id, workspace_dir), state)
    return state


def _attempt_count_for_failure(state: dict[str, Any], failure_id: str) -> int:
    return sum(
        1
        for item in state.get("attempts", [])
        if isinstance(item, dict) and item.get("failure_id") == failure_id
    )


def _attempt_status_from_step(
    status: Literal["succeeded", "failed", "skipped", "waiting_approval", "waiting_agent", "stopped"],
) -> Literal["running", "succeeded", "failed", "waiting_approval", "waiting_agent", "stopped"]:
    if status == "failed":
        return "failed"
    if status == "waiting_approval":
        return "waiting_approval"
    if status == "waiting_agent":
        return "waiting_agent"
    return "stopped"


async def _execute_plan_step(
    thread_id: str,
    workspace_dir: str | None,
    plan: RecoveryPlan,
    step: RecoveryPlanStep,
) -> RecoveryStepExecution:
    started_at = time.time()
    _emit_step_event(thread_id, workspace_dir, "failure_recovery_step_started", plan, step)
    result = RecoveryStepExecution(
        step_id=step.step_id,
        kind=step.kind,
        status="skipped",
        message="",
        started_at=started_at,
    )
    try:
        if step.requires_approval or step.kind == "ask_approval":
            result.status = "waiting_approval"
            result.message = step.reason or "恢复步骤需要用户审批。"
        elif step.kind == "ask_user":
            result.status = "stopped"
            result.message = step.reason or "恢复步骤需要用户补充信息。"
        elif step.kind == "stop":
            result.status = "stopped"
            result.message = step.reason or "恢复计划要求停止自动执行。"
        elif step.kind == "edit_file":
            result.status = "waiting_agent"
            result.message = "已创建 Coder 修复任务；恢复执行器不会自动盲改文件。"
            result.result = _create_agent_repair_task(thread_id, workspace_dir, plan, step)
        elif step.kind == "inspect_file":
            result = await _execute_inspect_file_step(thread_id, workspace_dir, step, result)
        elif step.kind == "rerun_command":
            result = await _execute_rerun_command_step(thread_id, workspace_dir, step, result)
        elif step.kind == "fallback_tool":
            result.status = "succeeded"
            result.message = "已记录降级工具策略，等待 Agent 在下一轮使用本地索引/内置工具继续。"
            result.result = {"target": step.target}
        else:
            result.status = "skipped"
            result.message = f"暂不支持执行步骤类型: {step.kind}"
    except Exception as exc:
        result.status = "failed"
        result.message = str(exc)
        result.result = {"error": str(exc)}
    finally:
        result.completed_at = time.time()
        _emit_step_event(thread_id, workspace_dir, "failure_recovery_step_completed", plan, step, result)
    return result


async def _execute_inspect_file_step(
    thread_id: str,
    workspace_dir: str | None,
    step: RecoveryPlanStep,
    result: RecoveryStepExecution,
) -> RecoveryStepExecution:
    if not step.target:
        result.status = "skipped"
        result.message = "没有可检查的文件目标。"
        return result
    from src.api.services.action_execution_service import execute_action_async

    action = await execute_action_async(
        kind="read_file",
        target=step.target,
        payload={"max_chars": 12_000},
        thread_id=thread_id,
        workspace_dir=str(_workspace(workspace_dir)),
    )
    if action.get("result") == "success":
        result.status = "succeeded"
        result.message = str(action.get("reason") or "文件检查完成。")
    else:
        result.status = "failed"
        result.message = str(action.get("reason") or "文件检查失败。")
    result.result = action
    return result


def _create_agent_repair_task(
    thread_id: str,
    workspace_dir: str | None,
    plan: RecoveryPlan,
    step: RecoveryPlanStep,
) -> dict[str, Any]:
    workspace = str(_workspace(workspace_dir))
    task_id = f"task-recovery-edit-{_safe_id(plan.failure_id or step.step_id)}"
    target = step.target or ""
    source_task_id = str(plan.source_task_id or "")
    goal_parts = [
        f"根据恢复计划 {plan.plan_id} 对失败 {plan.failure_id} 做最小范围修复。",
        f"失败类型：{plan.failure_type}。",
        "不要安装依赖、删除文件、修改环境变量或绕过审批。",
    ]
    if target:
        goal_parts.append(f"优先检查并修复相关文件：{target}。")
    goal_parts.append("修复完成后需要回到恢复计划重跑验证命令。")

    task_patch = {
        "reason": "failure_recovery_waiting_agent",
        "add_or_update_tasks": [
            {
                "id": task_id,
                "type": "recovery",
                "title": f"修复失败：{plan.failure_type}",
                "goal": "\n".join(goal_parts),
                "agent_role": "coder",
                "can_parallel": False,
                "writes_files": True,
                "resource_locks": [target] if target else [],
                "tool_policy": {
                    "allowed_tools": ["read_file", "search_codebase", "edit_file", "run_tests"],
                    "denied_tools": ["delete_file", "move_file", "git_operation"],
                    "approval_required_levels": ["risky_write", "shell_risky", "external_risky", "mcp_write"],
                    "notes": [
                        "Recovery edit task must be minimal and scoped to related files.",
                        "Dependency installation and destructive operations require user approval.",
                    ],
                },
                "context_policy": {
                    "mode": "failure_recovery",
                    "plan_id": plan.plan_id,
                    "failure_id": plan.failure_id,
                    "source_task_id": source_task_id,
                    "failed_task_id": source_task_id,
                    "failure_type": plan.failure_type,
                    "target": target,
                },
            }
        ],
        "metadata": {
            "latest_recovery_task_id": task_id,
            "latest_recovery_plan_id": plan.plan_id,
        },
    }
    task_board_data: dict[str, Any] = {}
    try:
        from src.api.services.run_state_service import patch_run_state

        board = patch_run_state(thread_id, workspace, task_patch)
        task_board_data = {
            "revision": board.revision,
            "task_count": len(board.nodes),
            "task": (board.task(task_id).model_dump(mode="json") if board.task(task_id) else None),
        }
    except Exception as exc:
        logger.warning(
            "recovery_repair_task_patch_failed",
            extra={"thread_id": thread_id, "task_id": task_id},
            exc_info=True,
        )
        task_board_data = {"error": str(exc)}

    loop_step_data: dict[str, Any] = {}
    try:
        from src.api.services.agent_loop_state_service import append_loop_step

        state = append_loop_step(
            thread_id,
            workspace,
            action={
                "type": "call_tool",
                "goal": f"Create recovery repair task {task_id}.",
                "agent": "Lead",
                "task_id": task_id,
                "tool_call": {
                    "tool": "task_create",
                    "input": {
                        "id": task_id,
                        "title": f"修复失败：{plan.failure_type}",
                        "owner": "coder",
                        "plan_id": plan.plan_id,
                        "failure_id": plan.failure_id,
                    },
                },
                "context_requirements": {
                    "mode": "failure_recovery",
                    "plan_id": plan.plan_id,
                    "failure_id": plan.failure_id,
                },
            },
            phase="recover",
            status="completed",
            summary=f"Recovery needs Coder repair task {task_id}.",
        )
        loop_step_data = {
            "current_step": state.current_step,
            "terminal_status": state.terminal_status,
            "active_agent": state.active_agent,
        }
    except Exception as exc:
        logger.warning(
            "recovery_loop_step_append_failed",
            extra={"thread_id": thread_id, "task_id": task_id},
            exc_info=True,
        )
        loop_step_data = {"error": str(exc)}

    get_event_store().append_event(
        thread_id=thread_id,
        event_type="failure_recovery_agent_task_created",
        title="恢复修复任务已创建",
        content=f"已创建 Coder 修复任务 {task_id}",
        agent="lead",
        payload={
            "task_id": task_id,
            "plan_id": plan.plan_id,
            "failure_id": plan.failure_id,
            "source_task_id": source_task_id,
            "failure_type": plan.failure_type,
            "target": target,
            "task_board": task_board_data,
            "loop_step": loop_step_data,
        },
        workspace_dir=workspace,
    )
    package_data: dict[str, Any] = {}
    try:
        package_data = _prepare_agent_repair_task(thread_id, workspace_dir, plan, task_id)
    except Exception as exc:
        logger.warning(
            "recovery_agent_task_prepare_failed",
            extra={"thread_id": thread_id, "task_id": task_id},
            exc_info=True,
        )
        package_data = {"error": str(exc)}
    return {
        "task_id": task_id,
        "target": target,
        "plan_id": plan.plan_id,
        "failure_id": plan.failure_id,
        "source_task_id": source_task_id,
        "task_board": task_board_data,
        "loop_step": loop_step_data,
        "package": package_data,
    }


def _latest_recovery_task_id(
    thread_id: str,
    workspace_dir: str | None,
    plan: RecoveryPlan,
) -> str:
    try:
        from src.api.services.run_state_service import get_or_create_run_state

        board = get_or_create_run_state(thread_id, str(_workspace(workspace_dir)))
    except Exception:
        return ""
    candidates = []
    for task in board.nodes:
        context_policy = task.context_policy if isinstance(task.context_policy, dict) else {}
        if (
            task.type == "recovery"
            and context_policy.get("mode") == "failure_recovery"
            and context_policy.get("plan_id") == plan.plan_id
        ):
            candidates.append(task)
    if not candidates:
        return ""
    preferred = next((task for task in candidates if task.status in {"ready", "pending", "running"}), candidates[-1])
    return preferred.id


def _prepare_agent_repair_task(
    thread_id: str,
    workspace_dir: str | None,
    plan: RecoveryPlan,
    task_id: str,
) -> dict[str, Any]:
    workspace = str(_workspace(workspace_dir))
    from src.api.services.run_state_service import build_task_context_pack, get_or_create_run_state
    from src.runtime.run_scheduler import mark_task_running
    from src.runtime.task_board import save_task_board

    board = get_or_create_run_state(thread_id, workspace)
    task = board.task(task_id)
    if not task:
        raise ValueError(f"Recovery task not found: {task_id}")
    if task.status in {"pending", "ready"}:
        mark_task_running(board, task_id)
        save_task_board(board, get_event_store().run_dir(thread_id, workspace))
    elif task.status != "running":
        raise ValueError(f"Recovery task is not runnable: {task.status}")

    context_pack = build_task_context_pack(thread_id, workspace, task_id)
    validation_command = _validation_command_for_plan(plan)
    package = {
        "package_id": f"repairpkg_{uuid.uuid4().hex[:12]}",
        "thread_id": thread_id,
        "workspace_dir": workspace,
        "task_id": task_id,
        "plan_id": plan.plan_id,
        "failure_id": plan.failure_id,
        "source_task_id": plan.source_task_id,
        "failure_type": plan.failure_type,
        "failure_class": plan.failure_class.value if isinstance(plan.failure_class, FailureClass) else str(plan.failure_class),
        "task": task.model_dump(mode="json"),
        "context_pack_id": context_pack.get("id"),
        "selected_files": context_pack.get("selected_files", []),
        "validation_command": validation_command,
        "allowed_tools": ["read_file", "search_codebase", "edit_file", "run_tests"],
        "blocked_actions": ["delete_file", "move_file", "git_operation", "install_dependency", "modify_env"],
        "system": _repair_agent_system(),
        "prompt": _repair_agent_prompt(plan, task.model_dump(mode="json"), context_pack, validation_command),
        "created_at": time.time(),
    }
    path = _repair_package_path(thread_id, workspace, task_id)
    _write_json_atomic(path, package)
    event = get_event_store().append_event(
        thread_id=thread_id,
        event_type="failure_recovery_agent_task_prepared",
        title="Coder 修复任务包已准备",
        content=f"{task_id}: {plan.failure_type}",
        agent="lead",
        payload={
            "task_id": task_id,
            "plan_id": plan.plan_id,
            "failure_id": plan.failure_id,
            "source_task_id": plan.source_task_id,
            "package_id": package["package_id"],
            "package_path": str(path),
            "context_pack_id": package["context_pack_id"],
            "validation_command": validation_command,
        },
        workspace_dir=workspace,
    )
    return {
        **package,
        "package_path": str(path),
        "event_id": event.id,
    }


def _repair_package_path(thread_id: str, workspace_dir: str, task_id: str) -> Path:
    root = get_event_store().run_dir(thread_id, workspace_dir) / "recovery_agent_tasks"
    return root / f"{_safe_id(task_id)}.json"


def _validation_command_for_plan(plan: RecoveryPlan) -> str:
    for step in plan.steps:
        if step.kind == "rerun_command" and step.command:
            return step.command
    return ""


def _repair_agent_system() -> str:
    return (
        "You are nanoCursor's Coder Recovery Agent. "
        "Make the smallest safe fix for the assigned failure. "
        "Never install dependencies, delete files, change secrets, or bypass approval. "
        "Use only the provided workspace context and allowed tools."
    )


def _repair_agent_prompt(
    plan: RecoveryPlan,
    task: dict[str, Any],
    context_pack: dict[str, Any],
    validation_command: str,
) -> str:
    selected_files = [
        str(item.get("path") or item.get("file") or item)
        for item in context_pack.get("selected_files", [])
        if item
    ][:12]
    return "\n".join([
        "请执行一个失败恢复修复任务。",
        "",
        f"任务 ID: {task.get('id')}",
        f"任务标题: {task.get('title')}",
        f"失败类型: {plan.failure_type}",
        f"失败摘要: {plan.summary}",
        f"目标文件: {(task.get('context_policy') or {}).get('target') or '未指定'}",
        f"验证命令: {validation_command or '无明确验证命令'}",
        "",
        "上下文文件:",
        "\n".join(f"- {path}" for path in selected_files) if selected_files else "- 暂无",
        "",
        "硬性要求:",
        "- 只做最小范围修复。",
        "- 不安装依赖，不删除或移动文件，不修改 .env / lockfile / Git 状态。",
        "- 如果缺少依赖或需要高风险操作，停止并说明需要用户确认。",
        "- 修复后必须说明改了什么，以及应重跑哪个验证命令。",
        "",
        "输出格式:",
        "- summary: 修复思路和结果",
        "- changed_files: 修改文件列表",
        "- validation: 建议运行的验证命令和预期",
        "- blocked: 如果无法安全修复，说明原因",
    ])


async def _run_recovery_agent_package(
    package: dict[str, Any],
    runner: RecoveryAgentRunner | None,
) -> str:
    tools = _recovery_agent_tools()
    if runner is None:
        from src.agent.engine import run_subagent

        runner = run_subagent
    result = runner(
        str(package.get("prompt") or ""),
        str(package.get("system") or ""),
        "CoderRecovery",
        tools,
    )
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


def _recovery_agent_tools() -> list[dict[str, Any]]:
    from src.agent.engine import TOOLS

    allowed = {"read_file", "edit_file", "list_directory", "run_tests"}
    return [
        tool
        for tool in TOOLS
        if isinstance(tool, dict) and str(tool.get("name") or "") in allowed
    ]


def _classify_recovery_agent_output(output: str) -> tuple[Literal["passed", "failed", "blocked"], str, str]:
    from src.tools.tool_result import is_tool_error_output, tool_error_message

    text = str(output or "").strip()
    if not text:
        return "failed", "Coder recovery task returned empty output.", "empty_output"
    if is_tool_error_output(text):
        error = tool_error_message(text)
        return "failed", error[:500], error

    blocked = re.search(r"(?im)^\s*-?\s*blocked\s*:\s*(.+)$", text)
    if blocked:
        reason = blocked.group(1).strip()
        if reason and reason.lower() not in {"none", "no", "n/a", "false", "无", "没有"}:
            return "blocked", reason[:500], reason

    summary = _extract_recovery_summary(text)
    return "passed", summary, ""


def _extract_recovery_summary(output: str) -> str:
    summary = re.search(r"(?im)^\s*-?\s*summary\s*:\s*(.+)$", output)
    if summary and summary.group(1).strip():
        return summary.group(1).strip()[:800]
    for line in output.splitlines():
        clean = line.strip(" -\t")
        if clean:
            return clean[:800]
    return "Coder recovery task completed."


def _next_action_after_agent_task(status: str, package: dict[str, Any]) -> dict[str, Any] | None:
    validation_command = str(package.get("validation_command") or "")
    if status == "passed" and validation_command:
        return {
            "type": "rerun_validation",
            "command": validation_command,
            "plan_id": package.get("plan_id"),
            "failure_id": package.get("failure_id"),
        }
    if status == "blocked":
        return {
            "type": "ask_user",
            "reason": "recovery_agent_blocked",
            "plan_id": package.get("plan_id"),
            "failure_id": package.get("failure_id"),
        }
    return None


async def _validate_recovery_agent_run(
    thread_id: str,
    workspace_dir: str,
    package: dict[str, Any],
    run: RecoveryAgentTaskRun,
) -> RecoveryAgentTaskRun:
    command = run.validation_command.strip()
    if not command:
        return run

    _emit_agent_task_event(
        thread_id,
        workspace_dir,
        "failure_recovery_validation_started",
        f"开始重跑验证命令：{command}",
        package,
        extra={"run_id": run.run_id, "command": command},
    )
    from src.api.services.action_execution_service import execute_action_async

    action = await execute_action_async(
        kind="run_command",
        target=command,
        payload={
            "timeout_seconds": 120,
            "max_stdout_chars": 40_000,
            "max_stderr_chars": 12_000,
            "recovery_context": {
                "mode": "failure_recovery",
                "plan_id": package.get("plan_id"),
                "failure_id": package.get("failure_id"),
                "task_id": package.get("task_id"),
                "run_id": run.run_id,
            },
        },
        thread_id=thread_id,
        workspace_dir=workspace_dir,
    )
    run.validation_result = action
    if action.get("requires_approval"):
        run.status = "blocked"
        run.summary = str(action.get("reason") or "验证命令需要用户审批。")
        run.error = "validation_requires_approval"
    elif action.get("result") == "success":
        run.status = "passed"
        run.summary = f"{run.summary}；验证命令已通过。" if run.summary else "修复已完成，验证命令已通过。"
        run.error = ""
    else:
        command_detail = _command_result_from_action(action)
        stderr = str(command_detail.get("stderr") or "")
        stdout = str(command_detail.get("stdout") or "")
        reason = stderr[:800] or stdout[:800] or str(action.get("reason") or "验证命令失败。")
        run.status = "failed"
        run.summary = f"修复已执行，但验证命令未通过：{reason[:300]}"
        run.error = reason
    run.next_action = _next_action_after_validation(run.status, package, command)
    if run.status == "failed":
        replan = _plan_recovery_from_validation_failure(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            package=package,
            run=run,
            action=action,
            command=command,
        )
        if replan and isinstance(run.next_action, dict):
            run.next_action["replanned"] = True
            run.next_action["new_plan_id"] = replan["plan"]["plan_id"]
            run.next_action["new_failure_id"] = replan["failure"]["failure_id"]
    run.completed_at = time.time()
    if run.started_at:
        run.duration_ms = int((run.completed_at - run.started_at) * 1000)
    _emit_agent_task_event(
        thread_id,
        workspace_dir,
        "failure_recovery_validation_completed",
        run.summary or run.error or "验证命令已结束",
        package,
        extra={
            "run_id": run.run_id,
            "command": command,
            "status": run.status,
            "validation_result": action,
        },
    )
    return run


def _command_result_from_action(action: dict[str, Any]) -> dict[str, Any]:
    """Return the command-runner result from an action pipeline response."""

    detail = action.get("detail") if isinstance(action.get("detail"), dict) else {}
    nested = detail.get("detail") if isinstance(detail.get("detail"), dict) else None
    command_detail = nested or detail
    return command_detail if isinstance(command_detail, dict) else {}


def _plan_recovery_from_validation_failure(
    *,
    thread_id: str,
    workspace_dir: str,
    package: dict[str, Any],
    run: RecoveryAgentTaskRun,
    action: dict[str, Any],
    command: str,
) -> dict[str, Any] | None:
    """Create a follow-up recovery plan from a failed validation rerun.

    This intentionally only plans the next recovery step. It does not execute
    another repair pass automatically, which keeps retry behavior bounded and
    reviewable.
    """

    command_detail = _command_result_from_action(action)
    if not command_detail:
        command_detail = {
            "command": command,
            "stderr": run.error,
            "exit_code": 1,
        }
    tool_result = {
        "tool_name": "shell",
        "event_type": "failure_recovery_validation_failed",
        "event_id": action.get("action_id"),
        "agent": "tester",
        "command": command_detail.get("command") or command,
        "cwd": command_detail.get("cwd"),
        "exit_code": command_detail.get("exit_code"),
        "timed_out": command_detail.get("timed_out"),
        "stdout": command_detail.get("stdout") or "",
        "stderr": command_detail.get("stderr") or run.error,
        "message": run.summary or str(action.get("reason") or ""),
        "task_id": package.get("task_id"),
        "source": "validation_replan",
        "parent_plan_id": package.get("plan_id"),
        "parent_failure_id": package.get("failure_id"),
        "agent_task_run_id": run.run_id,
    }
    evidence = build_command_failure_evidence(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        tool_result=tool_result,
    )
    failure = classify_command_failure(evidence)
    plan = build_recovery_plan(thread_id, workspace_dir, failure, evidence)
    state = _append_plan(thread_id, workspace_dir, evidence, failure, plan)
    get_event_store().append_event(
        thread_id=thread_id,
        event_type="failure_evidence_built",
        title="验证失败证据已整理",
        content=evidence.message or evidence.stderr_tail or evidence.stdout_tail,
        agent="system",
        payload={
            **evidence.model_dump(mode="json"),
            "source": "validation_replan",
            "parent_plan_id": package.get("plan_id"),
            "parent_failure_id": package.get("failure_id"),
            "agent_task_run_id": run.run_id,
        },
        workspace_dir=workspace_dir,
    )
    get_event_store().append_event(
        thread_id=thread_id,
        event_type="failure_recovery_planned",
        title=plan.title,
        content=f"验证失败后已生成下一轮恢复计划：{plan.summary}",
        agent="lead",
        payload={
            **plan.model_dump(mode="json"),
            "source": "validation_replan",
            "parent_plan_id": package.get("plan_id"),
            "parent_failure_id": package.get("failure_id"),
            "agent_task_run_id": run.run_id,
        },
        workspace_dir=workspace_dir,
    )
    return {
        "thread_id": thread_id,
        "workspace_dir": workspace_dir,
        "evidence": evidence.model_dump(mode="json"),
        "failure": failure.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "state": state,
    }


def _next_action_after_validation(
    status: str,
    package: dict[str, Any],
    command: str,
) -> dict[str, Any] | None:
    if status == "failed":
        return {
            "type": "replan_recovery",
            "reason": "validation_failed",
            "command": command,
            "plan_id": package.get("plan_id"),
            "failure_id": package.get("failure_id"),
        }
    if status == "blocked":
        return {
            "type": "ask_approval",
            "reason": "validation_requires_approval",
            "command": command,
            "plan_id": package.get("plan_id"),
            "failure_id": package.get("failure_id"),
        }
    return None


def _complete_recovery_agent_task(
    thread_id: str,
    workspace_dir: str,
    package: dict[str, Any],
    run: RecoveryAgentTaskRun,
) -> dict[str, Any]:
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.run_scheduler import TaskExecutionResult, apply_task_result
    from src.runtime.task_board import save_task_board

    board = get_or_create_run_state(thread_id, workspace_dir)
    result = TaskExecutionResult(
        task_id=run.task_id,
        status=run.status,
        summary=run.summary,
        outputs=[
            {
                "kind": "recovery_agent_output",
                "content": run.output,
                "package_id": run.package_id,
                "run_id": run.run_id,
                "created_at": time.time(),
            }
        ] if run.output else [],
        evidence=[
            {
                "kind": "recovery_agent_run",
                "plan_id": run.plan_id,
                "failure_id": run.failure_id,
                "package_id": run.package_id,
                "run_id": run.run_id,
                "validation_command": run.validation_command,
                "next_action": run.next_action,
            }
        ],
        failure_category=("recovery_agent_blocked" if run.status == "blocked" else None),
        retryable=run.status == "failed",
    )
    apply_task_result(board, result)
    advanced_source_tasks: list[dict[str, Any]] = []
    if run.status == "passed":
        advanced_source_tasks = _advance_recovered_source_tasks(board, package, run)
    save_task_board(board, get_event_store().run_dir(thread_id, workspace_dir))
    task = board.task(run.task_id)
    if advanced_source_tasks:
        _emit_agent_task_event(
            thread_id,
            workspace_dir,
            "failure_recovery_source_task_advanced",
            f"已将 {len(advanced_source_tasks)} 个原始失败任务标记为已恢复。",
            package,
            extra={"run_id": run.run_id, "advanced_source_tasks": advanced_source_tasks},
        )
    return {
        "revision": board.revision,
        "board_status": board.status,
        "task": task.model_dump(mode="json") if task else None,
        "result": result.model_dump(mode="json"),
        "advanced_source_tasks": advanced_source_tasks,
        "package_id": package.get("package_id"),
    }


def _advance_recovered_source_tasks(
    board: Any,
    package: dict[str, Any],
    run: RecoveryAgentTaskRun,
) -> list[dict[str, Any]]:
    """Mark the original failed task recovered when the link is explicit or unambiguous."""

    from src.runtime.run_scheduler import TaskExecutionResult, apply_task_result

    candidates = _recovered_source_task_candidates(board, package, run)
    advanced: list[dict[str, Any]] = []
    for task in candidates:
        if task.id == run.task_id or task.type == "recovery":
            continue
        if task.status not in {"failed", "blocked"}:
            continue
        result = TaskExecutionResult(
            task_id=task.id,
            status="passed",
            summary=f"失败已由恢复任务 {run.task_id} 修复，并通过验证命令。",
            evidence=[
                {
                    "kind": "recovered_by_failure_recovery",
                    "recovery_task_id": run.task_id,
                    "plan_id": run.plan_id,
                    "failure_id": run.failure_id,
                    "run_id": run.run_id,
                    "validation_command": run.validation_command,
                    "created_at": time.time(),
                }
            ],
            outputs=[
                {
                    "kind": "recovery_source_task_advanced",
                    "content": run.summary,
                    "recovery_task_id": run.task_id,
                    "created_at": time.time(),
                }
            ],
            retryable=False,
        )
        apply_task_result(board, result)
        advanced.append({
            "task_id": task.id,
            "title": task.title,
            "previous_status": "failed_or_blocked",
            "status": "passed",
        })
    return advanced


def _recovered_source_task_candidates(
    board: Any,
    package: dict[str, Any],
    run: RecoveryAgentTaskRun,
) -> list[Any]:
    task_data = package.get("task") if isinstance(package.get("task"), dict) else {}
    context_policy = task_data.get("context_policy") if isinstance(task_data.get("context_policy"), dict) else {}
    explicit_ids = [
        package.get("source_task_id"),
        package.get("failed_task_id"),
        context_policy.get("source_task_id"),
        context_policy.get("failed_task_id"),
    ]
    for task_id in explicit_ids:
        text = str(task_id or "").strip()
        if not text:
            continue
        task = board.task(text)
        if task:
            return [task]

    failure_id = str(run.failure_id or package.get("failure_id") or "")
    if failure_id:
        matched = []
        for task in board.nodes:
            policy = task.context_policy if isinstance(task.context_policy, dict) else {}
            if task.id != run.task_id and task.type != "recovery" and policy.get("failure_id") == failure_id:
                matched.append(task)
        if matched:
            return matched

    failed = [
        task
        for task in board.nodes
        if task.id != run.task_id and task.type != "recovery" and task.status == "failed"
    ]
    return failed if len(failed) == 1 else []


def _persist_recovery_agent_run(
    thread_id: str,
    workspace_dir: str,
    task_id: str,
    run: RecoveryAgentTaskRun,
) -> None:
    path = _repair_run_path(thread_id, workspace_dir, task_id, run.run_id)
    _write_json_atomic(path, run.model_dump(mode="json"))

    state = get_recovery_loop_state(thread_id, workspace_dir)
    runs = state.setdefault("agent_task_runs", [])
    if isinstance(runs, list):
        runs.append({**run.model_dump(mode="json"), "run_path": str(path)})
    else:
        state["agent_task_runs"] = [{**run.model_dump(mode="json"), "run_path": str(path)}]
    state["latest_agent_task_run_id"] = run.run_id
    state["status"] = f"agent_repair_{run.status}"
    state["updated_at"] = time.time()
    _write_json_atomic(_state_path(thread_id, workspace_dir), state)


def _repair_run_path(thread_id: str, workspace_dir: str, task_id: str, run_id: str) -> Path:
    root = get_event_store().run_dir(thread_id, workspace_dir) / "recovery_agent_tasks"
    return root / f"{_safe_id(task_id)}.{_safe_id(run_id)}.json"


def _emit_agent_task_event(
    thread_id: str,
    workspace_dir: str | None,
    event_type: str,
    content: str,
    package: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "task_id": package.get("task_id"),
        "plan_id": package.get("plan_id"),
        "failure_id": package.get("failure_id"),
        "package_id": package.get("package_id"),
    }
    if extra:
        payload.update(extra)
    get_event_store().append_event(
        thread_id=thread_id,
        event_type=event_type,
        title=_event_title(event_type),
        content=content,
        agent="coder",
        payload=payload,
        workspace_dir=workspace_dir,
    )


async def _execute_rerun_command_step(
    thread_id: str,
    workspace_dir: str | None,
    step: RecoveryPlanStep,
    result: RecoveryStepExecution,
) -> RecoveryStepExecution:
    if not step.command:
        result.status = "skipped"
        result.message = "没有可重跑的命令。"
        return result
    from src.api.services.action_execution_service import execute_action_async

    _emit_step_event(
        thread_id,
        workspace_dir,
        "failure_recovery_rerun_started",
        None,
        step,
        extra={"command": step.command},
    )
    action = await execute_action_async(
        kind="run_command",
        target=step.command,
        payload={
            "timeout_seconds": 120,
            "max_stdout_chars": 40_000,
            "max_stderr_chars": 12_000,
        },
        thread_id=thread_id,
        workspace_dir=str(_workspace(workspace_dir)),
    )
    if action.get("requires_approval"):
        result.status = "waiting_approval"
        result.message = str(action.get("reason") or "命令重跑需要审批。")
    elif action.get("result") == "success":
        result.status = "succeeded"
        result.message = "验证命令重跑成功。"
    else:
        result.status = "failed"
        detail = action.get("detail") if isinstance(action.get("detail"), dict) else {}
        command_detail = detail.get("detail") if isinstance(detail.get("detail"), dict) else {}
        stderr = str(command_detail.get("stderr") or "")
        result.message = stderr[:500] or str(action.get("reason") or "验证命令重跑失败。")
    result.result = action
    return result


def _emit_recovery_event(
    thread_id: str,
    workspace_dir: str | None,
    event_type: str,
    content: str,
    attempt: RecoveryAttempt,
) -> None:
    get_event_store().append_event(
        thread_id=thread_id,
        event_type=event_type,
        title=_event_title(event_type),
        content=content,
        agent="lead",
        payload=attempt.model_dump(mode="json"),
        workspace_dir=workspace_dir,
    )


def _emit_step_event(
    thread_id: str,
    workspace_dir: str | None,
    event_type: str,
    plan: RecoveryPlan | None,
    step: RecoveryPlanStep,
    result: RecoveryStepExecution | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "plan_id": plan.plan_id if plan else "",
        "failure_id": plan.failure_id if plan else "",
        "step": step.model_dump(mode="json"),
    }
    if result:
        payload["result"] = result.model_dump(mode="json")
    if extra:
        payload.update(extra)
    get_event_store().append_event(
        thread_id=thread_id,
        event_type=event_type,
        title=_event_title(event_type),
        content=result.message if result else step.title,
        agent=step.agent or "lead",
        payload=payload,
        workspace_dir=workspace_dir,
    )


def _event_title(event_type: str) -> str:
    titles = {
        "failure_recovery_started": "失败恢复开始",
        "failure_recovery_step_started": "恢复步骤开始",
        "failure_recovery_step_completed": "恢复步骤完成",
        "failure_recovery_rerun_started": "开始重跑验证命令",
        "failure_recovery_waiting_approval": "失败恢复等待审批",
        "failure_recovery_agent_task_started": "Coder 修复任务开始",
        "failure_recovery_agent_task_completed": "Coder 修复任务完成",
        "failure_recovery_agent_task_failed": "Coder 修复任务失败",
        "failure_recovery_validation_started": "恢复验证开始",
        "failure_recovery_validation_completed": "恢复验证完成",
        "failure_recovery_source_task_advanced": "原始失败任务已恢复",
        "failure_recovery_succeeded": "失败恢复完成",
        "failure_recovery_failed": "失败恢复失败",
        "failure_recovery_stopped": "失败恢复停止",
    }
    return titles.get(event_type, event_type)


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _select_failure_event(
    thread_id: str,
    workspace_dir: str | None,
    event_id: str | None,
) -> AgentEvent | None:
    events = get_event_store().list_events(thread_id, workspace_dir)
    if event_id:
        return next((event for event in events if event.id == event_id), None)
    for event in reversed(events):
        if _is_failure_event(event):
            return event
    return None


def _is_failure_event(event: AgentEvent) -> bool:
    if event.type in {"error", "tool_call_failed", "command_failed", "approval_wait"}:
        return True
    payload = event.payload or {}
    status = str(payload.get("status") or payload.get("result") or "").lower()
    if status in {"failed", "failure", "error", "denied"}:
        return True
    if payload.get("ok") is False:
        return True
    exit_code = _first_int(payload, ("exit_code", "returncode", "return_code"))
    return exit_code not in (None, 0)


def _event_payload(event: AgentEvent) -> dict[str, Any]:
    payload = dict(event.payload or {})
    payload.setdefault("event_id", event.id)
    payload.setdefault("event_type", event.type)
    payload.setdefault("agent", event.agent)
    payload.setdefault("title", event.title)
    payload.setdefault("message", event.content)
    return payload


def _payload_text(payload: dict[str, Any], event: AgentEvent | None = None) -> str:
    pieces: list[str] = []
    if event:
        pieces.extend([event.type, event.title, event.content])
    for value in payload.values():
        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, (int, float, bool)):
            pieces.append(str(value))
        elif isinstance(value, dict):
            pieces.extend(str(v) for v in value.values() if isinstance(v, (str, int, float, bool)))
    return "\n".join(piece for piece in pieces if piece)


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            nested = _first_text(value, ("text", "message", "content", "stdout", "stderr"))
            if nested:
                return nested
    return None


def _nested_text(payload: dict[str, Any], path: tuple[str, str]) -> str | None:
    first, second = path
    value = payload.get(first)
    if isinstance(value, dict):
        nested = value.get(second)
        if isinstance(nested, str):
            return nested
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                continue
    return None


def _first_bool(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "yes", "1"}:
            return True
    return False


def _tail(text: str | None, limit: int = _TAIL_LIMIT) -> str:
    if not text:
        return ""
    return text[-limit:]


def _safe_excerpt(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "event_id",
        "event_type",
        "stage_id",
        "task_id",
        "agent",
        "tool_name",
        "tool",
        "command",
        "cwd",
        "exit_code",
        "returncode",
        "status",
        "ok",
    }
    return {key: value for key, value in payload.items() if key in allowed}


def _related_files_from_text(text: str, workspace: Path) -> list[str]:
    candidates: list[str] = []
    patterns = (
        r"(?P<path>[\w./\\-]+\.py)",
        r"(?P<path>[\w./\\-]+\.tsx?)",
        r"(?P<path>[\w./\\-]+\.jsx?)",
        r"(?P<path>[\w./\\-]+\.md)",
        r"(?P<path>[\w./\\-]+\.json)",
        r"(?P<path>[\w./\\-]+\.ya?ml)",
        r"(?P<path>[\w./\\-]+\.toml)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            raw = match.group("path").strip("`'\".,:;)")
            rel = _normalize_related_file(raw, workspace)
            if rel and rel not in candidates:
                candidates.append(rel)
    return candidates[:12]


def _normalize_related_file(raw: str, workspace: Path) -> str | None:
    try:
        path = Path(raw)
        resolved = path.resolve() if path.is_absolute() else (workspace / raw).resolve()
        resolved.relative_to(workspace)
        return resolved.relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return None


def _suggest_actions_for_failure_type(
    failure_type: str,
    evidence: CommandFailureEvidence,
) -> list[SuggestedAction]:
    if failure_type in {"pytest_assertion_failure", "python_syntax_error"}:
        return [
            SuggestedAction(label="检查相关文件", mode="auto", description="读取失败文件和最近修改。"),
            SuggestedAction(label="局部修复后复测", mode="confirm", description="只修改相关文件并重新运行失败命令。"),
        ]
    if failure_type == "module_not_found":
        return [
            SuggestedAction(label="确认依赖来源", mode="manual", description="区分本地模块缺失和外部依赖缺失。"),
        ]
    if failure_type in {"tool_policy_blocked", "permission_denied"}:
        return [
            SuggestedAction(label="请求授权", mode="manual", description="高风险或权限不足操作需要用户确认。"),
        ]
    if failure_type == "command_not_found":
        return [
            SuggestedAction(label="切换命令或安装工具", mode="manual", description="命令不存在，不能自动安装。"),
        ]
    if evidence.command:
        return [
            SuggestedAction(label="整理证据后重试", mode="confirm", description="基于命令输出缩小重试范围。"),
        ]
    return [
        SuggestedAction(label="补充失败证据", mode="manual", description="失败信息不足，需要更多上下文。"),
    ]


def _failure_title(failure_type: str) -> str:
    titles = {
        "pytest_assertion_failure": "测试断言失败",
        "python_syntax_error": "Python 代码语法失败",
        "module_not_found": "依赖或本地模块缺失",
        "permission_denied": "权限不足",
        "tool_policy_blocked": "工具策略阻断",
        "command_not_found": "命令不存在",
        "path_not_found": "路径不存在",
        "timeout": "命令执行超时",
        "mcp_unavailable": "MCP 服务不可用",
        "non_zero_exit": "命令非零退出",
    }
    return titles.get(failure_type, "未知命令失败")


def _failure_summary(failure_type: str, evidence: CommandFailureEvidence) -> str:
    command = f"命令 `{evidence.command}` " if evidence.command else ""
    files = f"，相关文件：{', '.join(evidence.related_files[:3])}" if evidence.related_files else ""
    return f"{command}触发了{_failure_title(failure_type)}{files}。"


def _plan_summary(
    failure_type: str,
    can_auto_recover: bool,
    steps: list[RecoveryPlanStep],
) -> str:
    mode = "可进入受控自动恢复" if can_auto_recover else "需要用户确认后继续"
    return f"{_failure_title(failure_type)}已生成 {len(steps)} 步恢复计划，{mode}。"


def _safe_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower()
    return slug[:80] or uuid.uuid4().hex[:8]


def _rerun_step(evidence: CommandFailureEvidence, title: str) -> RecoveryPlanStep:
    return RecoveryPlanStep(
        kind="rerun_command",
        title=title,
        reason="复测必须使用原失败命令或等价的最小验证命令。",
        agent="tester",
        command=evidence.command,
        risk="medium",
    )
