"""Create and start a standard nanoCursor run."""

from __future__ import annotations

import queue
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.api.models import Message, RunRequest, RunResponse
from src.api.run_state import (
    active_runs,
    emit_agenthub_event,
    emit_stage_updates,
    event_store,
    get_workspace,
    run_manager,
    runs_lock,
    set_active_workspace,
    sync_run_context,
)
from src.api.services.intent_router import classify_user_intent_async
from src.api.services.intent_runtime_context import IntentRuntimeContext
from src.api.services.routing_decision_service import build_routing_decision
from src.api.services.run_context import RunContext
from src.api.services.run_rate_limit_service import check_run_start_rate_limit
from src.api.services.workflow_thread_service import start_workflow_thread
from src.infra import config as config_module
from src.infra.messages import AIMessage, HumanMessage


def messages_for_run(messages: list[Message] | None, prompt: str) -> list[Any]:
    """Convert the recent API message history into runtime message objects."""
    history: list[Any] = []
    for message in (messages or [])[-12:]:
        role = str(message.role or "user").lower()
        content = str(message.content or "").strip()
        if not content:
            continue
        if role == "assistant":
            history.append(AIMessage(content=content))
        else:
            history.append(HumanMessage(content=content))
    history.append(HumanMessage(content=prompt))
    return history


def intent_session_fields(intent_decision: dict[str, Any] | None) -> dict[str, Any]:
    """Return the durable intent metadata stored with a run session."""
    intent = intent_decision if isinstance(intent_decision, dict) else {}
    raw_decision = intent.get("raw_decision") if isinstance(intent.get("raw_decision"), dict) else {}
    return {
        "intent_decision": intent,
        "intent_decision_normalized": intent,
        "intent_decision_raw": raw_decision,
        "intent_guard_hits": intent.get("guard_hits") if isinstance(intent.get("guard_hits"), list) else [],
        "intent_corrections": [],
    }


def intent_context_from_run_request(request: RunRequest, workspace_dir: str) -> IntentRuntimeContext:
    """Build compact intent context for standalone run starts.

    Conversation-scoped runs have richer persisted context. Standalone runs
    still benefit from recent message history and workspace identity, especially
    when semantic routing is enabled.
    """

    last_user = ""
    last_assistant = ""
    for message in reversed(request.messages or []):
        role = str(message.role or "user").lower()
        content = str(message.content or "").strip()
        if role == "assistant" and not last_assistant:
            last_assistant = content
        elif role == "user" and not last_user and content != request.prompt:
            last_user = content
        if last_user and last_assistant:
            break
    return IntentRuntimeContext(
        conversation_id=str(request.conversation_id or ""),
        thread_id=str(request.thread_id or ""),
        workspace_dir=workspace_dir,
        last_user_message=last_user,
        last_assistant_message=last_assistant,
        workspace_is_git=(Path(workspace_dir) / ".git").exists() if workspace_dir else False,
    )


async def start_standard_run(
    request: RunRequest,
    *,
    workflow_runner: Callable[..., Any] | None = None,
) -> RunResponse:
    """Register, persist, initialize, and start one standard run."""
    prompt = request.prompt
    thread_id = request.thread_id or str(uuid.uuid4())

    if request.workspace_dir:
        set_active_workspace(request.workspace_dir)

    allowed, rate_limit_msg = check_run_start_rate_limit(
        thread_id,
        active_runs=active_runs,
        runs_lock=runs_lock,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail=rate_limit_msg)

    with runs_lock:
        running_count = sum(1 for run in active_runs.values() if run.get("status") == "running")
    if running_count >= config_module.MAX_CONCURRENT_RUNS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"系统繁忙，当前已有 {running_count} 个运行中的任务"
                f"（上限 {config_module.MAX_CONCURRENT_RUNS}）。请稍后再试。"
            ),
        )

    initial_messages = messages_for_run(request.messages, prompt)
    run_workspace = get_workspace()
    run_team = list(request.team or [])
    run_execution_plan = dict(request.execution_plan or {})
    if "intent_decision" not in run_execution_plan:
        run_execution_plan["intent_decision"] = await classify_user_intent_async(
            prompt,
            runtime_context=intent_context_from_run_request(request, run_workspace),
        )
    if "routing_decision" not in run_execution_plan:
        run_execution_plan["routing_decision"] = build_routing_decision(
            prompt,
            workspace_dir=run_workspace,
            intent_decision=run_execution_plan.get("intent_decision", {}),
            execution_plan=run_execution_plan,
            team=run_team,
        )

    run_context = RunContext(
        thread_id=thread_id,
        workspace_dir=run_workspace,
        queue=queue.Queue(),
        status="running",
        conversation_id=request.conversation_id,
        team=run_team,
        execution_plan=run_execution_plan,
    )
    with runs_lock:
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=thread_id,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
    )
    session_metadata = run_context.session_metadata()
    if session_metadata:
        event_store.update_session(thread_id, run_workspace, **session_metadata)
    event_store.update_session(
        thread_id,
        run_workspace,
        **intent_session_fields(run_execution_plan.get("intent_decision", {})),
        routing_decision=run_execution_plan.get("routing_decision", {}),
    )
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="intent_routed",
        title="用户意图已路由",
        content=str(run_execution_plan.get("intent_decision", {}).get("rationale", "")),
        agent="lead",
        payload={"intent_decision": run_execution_plan.get("intent_decision", {})},
        workspace_dir=run_workspace,
    )
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="routing_decision_built",
        title="运行决策已生成",
        content=str(run_execution_plan.get("routing_decision", {}).get("reason", "")),
        agent="lead",
        payload={"routing_decision": run_execution_plan.get("routing_decision", {})},
        workspace_dir=run_workspace,
    )

    try:
        from src.api.services.agent_loop_state_service import init_agent_loop_state

        intent_decision = run_execution_plan.get("intent_decision", {})
        init_agent_loop_state(
            thread_id,
            run_workspace,
            user_request=prompt,
            intent=intent_decision,
            conversation_id=request.conversation_id,
        )
    except Exception:
        pass

    try:
        from src.api.services.run_state_service import get_or_create_run_state

        get_or_create_run_state(thread_id, run_workspace)
    except Exception as exc:
        emit_agenthub_event(
            thread_id=thread_id,
            event_type="run_state_create_failed",
            title="运行状态创建失败",
            content=str(exc),
            agent="system",
            payload={"error": str(exc)},
            workspace_dir=run_workspace,
        )

    stage_updates = run_context.start_first_stage()
    sync_run_context(thread_id, run_workspace)
    emit_stage_updates(thread_id, run_workspace, stage_updates)
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="任务已启动",
        content=prompt,
        payload={
            "workspace_dir": run_workspace,
            "thread_id": thread_id,
            "conversation_id": request.conversation_id,
        },
        workspace_dir=run_workspace,
    )

    start_workflow_thread(
        thread_id=thread_id,
        initial_messages=initial_messages,
        workspace_dir=run_workspace,
        run_context=run_context,
        workflow_runner=workflow_runner,
    )

    return RunResponse(thread_id=thread_id, status="started")
