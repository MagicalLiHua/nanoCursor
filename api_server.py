"""
nanoCursor API Server - FastAPI 后端

提供给 React 前端的 REST + SSE 接口。
主要功能：
- 启动 agent_loop 工作流并流式返回事件 (SSE)
- 提供文件浏览、指标、配置等数据接口
"""

import asyncio
import json
import os
import queue
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.infra.messages import AIMessage, HumanMessage

from src.api.models import (
    AgentEvent,
    BenchmarkRunRequest,
    CancelResponse,
    ConversationCreateRequest,
    ConversationRunRequest,
    ConversationTeamRecommendRequest,
    ConversationTeamUpdateRequest,
    Message,
    RetryRunRequest,
    RunRequest,
    RunResponse,
)


# Ensure project root is in sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Create app via factory (health/ready/version, CORS, middleware, error handlers included)
from src.api.app import create_app
app = create_app()

from src.agent.engine import agent_loop, agent_loop_stream, run_subagent, TOOLS, get_workdir
from src.agent.file_lock import get_file_lock, cleanup_file_lock
import src.infra.config as config_module
from src.infra.metrics import metrics as metrics_collector

from src.api.services.workspace_runtime_service import (
    get_active_workspace,
    set_active_workspace,
)

# Thin aliases for internal use by remaining inline routes
_get_workspace = get_active_workspace
_set_active_workspace = set_active_workspace


def _workspace_for_thread(thread_id: str) -> str:
    with runs_lock:
        run_info = active_runs.get(thread_id)
        workspace_dir = run_info.get("workspace_dir") if run_info else None
    if workspace_dir:
        return workspace_dir

    store = globals().get("event_store")
    if store is not None:
        try:
            indexed_workspace = store.workspace_for_thread(thread_id)
            if indexed_workspace and store.get_session(thread_id, indexed_workspace):
                return indexed_workspace

            session = store.get_session(thread_id)
            if session and session.get("workspace_dir"):
                return str(Path(session["workspace_dir"]).resolve())

            from src.api.services.workspace_registry_service import list_recent_projects
            for item in list_recent_projects():
                candidate = item.get("path") if isinstance(item, dict) else None
                if candidate and store.get_session(thread_id, candidate):
                    return str(Path(candidate).resolve())
        except Exception:
            pass

    return _get_workspace()


def _session_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Resolve a run session from the active workspace, runtime index, or recent projects."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        workspace_dir = run_info.get("workspace_dir") if run_info else None
    if workspace_dir:
        session = event_store.get_session(thread_id, workspace_dir)
        if session:
            return session

    try:
        indexed_workspace = event_store.workspace_for_thread(thread_id)
    except Exception:
        indexed_workspace = None
    if indexed_workspace:
        session = event_store.get_session(thread_id, indexed_workspace)
        if session:
            return session

    try:
        current_workspace = str(Path(_get_workspace()).resolve())
        session = event_store.get_session(thread_id, current_workspace)
        if session:
            return session
    except Exception:
        pass

    try:
        from src.api.services.workspace_registry_service import list_recent_projects
        for item in list_recent_projects():
            candidate = item.get("path") if isinstance(item, dict) else None
            if not candidate:
                continue
            session = event_store.get_session(thread_id, candidate)
            if session:
                return session
    except Exception:
        pass
    return None


def _should_cancel_run(thread_id: str) -> bool:
    sm = run_manager.get_state_machine(thread_id)
    if sm and sm.status in {
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
    }:
        _cancel_agent_pool(thread_id)
        return True
    with runs_lock:
        run_info = active_runs.get(thread_id)
        should_cancel = bool(run_info and run_info.get("status") in {"cancelling", "cancelled", "failed", "interrupted"})
    if should_cancel:
        _cancel_agent_pool(thread_id)
    return should_cancel


def _cancel_agent_pool(thread_id: str):
    """Cancel all sub-agents when a run is being cancelled."""
    try:
        from src.agent.agent_pool import get_pool
        pool = get_pool(thread_id)
        if pool:
            pool.cancel_all()
    except Exception:
        pass


def _retry_context_for_run(
    thread_id: str,
    workspace_dir: str,
    failure_id: str | None = None,
) -> dict[str, Any]:
    """Collect compact failure/lifecycle evidence for a retry run."""
    session = _session_for_thread(thread_id) or {}
    lifecycle = session.get("lifecycle") if isinstance(session.get("lifecycle"), dict) else {}
    failures: list[Any] = []
    try:
        from src.api.services.failure_classifier_service import load_failures, save_failures
        failures = load_failures(thread_id, workspace_dir)
        if not failures and session.get("status") == "failed":
            failures = save_failures(thread_id, workspace_dir)
    except Exception:
        failures = []

    selected_failure = None
    if failure_id:
        selected_failure = next((item for item in failures if item.failure_id == failure_id), None)
    if selected_failure is None and failures:
        selected_failure = failures[0]

    events = event_store.list_events(thread_id, workspace_dir)
    error_events = [event for event in events if event.type in {"error", "tool_policy_blocked", "test_finished"}]
    latest_errors = error_events[-3:]
    failed_stage_id = lifecycle.get("failed_stage_id")
    stages = session.get("execution_plan", {}).get("stages", []) if isinstance(session.get("execution_plan"), dict) else []
    failed_stage = next(
        (stage for stage in stages if isinstance(stage, dict) and stage.get("id") == failed_stage_id),
        None,
    )

    return {
        "status": session.get("status", ""),
        "failed_stage_id": failed_stage_id or "",
        "failed_stage": failed_stage or {},
        "failure": selected_failure.model_dump() if selected_failure else {},
        "recent_errors": [
            {
                "type": event.type,
                "title": event.title,
                "content": event.content[:800],
                "payload": event.payload,
            }
            for event in latest_errors
        ],
    }


def _build_retry_prompt(
    *,
    original_prompt: str,
    original_thread_id: str,
    original_status: str,
    retry_mode: str,
    retry_context: dict[str, Any],
    instruction: str = "",
) -> str:
    failed_stage = retry_context.get("failed_stage") or {}
    failure = retry_context.get("failure") or {}
    recent_errors = retry_context.get("recent_errors") or []
    lines = [
        "这是一次 nanoCursor 重试运行，请基于原始需求和失败证据继续完成任务。",
        "",
        f"原始 Run: {original_thread_id}",
        f"原始状态: {original_status}",
        f"重试模式: {retry_mode}",
        "",
        "原始需求:",
        original_prompt or "(无原始需求)",
    ]
    if retry_mode == "failed_stage" and failed_stage:
        lines.extend([
            "",
            "优先重试失败阶段:",
            f"- 阶段: {failed_stage.get('title') or failed_stage.get('id')}",
            f"- 负责人: {failed_stage.get('owner') or 'Lead'}",
            f"- 失败原因: {failed_stage.get('failure') or retry_context.get('failed_stage_id') or '未知'}",
        ])
    if failure:
        evidence = failure.get("evidence") if isinstance(failure.get("evidence"), dict) else {}
        lines.extend([
            "",
            "失败分类:",
            f"- 类型: {failure.get('failure_class') or 'unknown'}",
            f"- 标题: {failure.get('title') or '运行失败'}",
            f"- 证据: {json.dumps(evidence, ensure_ascii=False)[:1200]}",
        ])
    if recent_errors:
        lines.append("\n最近错误事件:")
        for event in recent_errors:
            lines.append(f"- [{event.get('type')}] {event.get('title')}: {event.get('content')}")
    if instruction.strip():
        lines.extend(["", "用户补充指令:", instruction.strip()])
    lines.extend([
        "",
        "执行要求:",
        "- 不要盲目重复上次失败路径，先复盘失败原因。",
        "- 只修改和本次需求相关的文件。",
        "- 如涉及代码修改，完成后给出验证命令和结果。",
        "- 最终回复说明本次重试相对原 run 的修复点、风险和下一步。",
    ])
    return "\n".join(lines)


def _messages_for_run(messages: list[Message] | None, prompt: str) -> list[Any]:
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


def _is_simple_lead_message(prompt: str) -> bool:
    return is_lead_direct_intent(prompt)


def _lead_only_execution_plan(prompt: str, workspace_dir: str, team: list[dict[str, Any]]) -> dict[str, Any]:
    lead = team[0] if team else {"name": "Lead", "role": "lead"}
    stage = {
        "id": "lead_reply",
        "title": "Lead 直接回复",
        "owner": lead.get("name") or "Lead",
        "owner_role": lead.get("role") or "lead",
        "description": "判断为轻量对话，不启动完整交付流水线；由 Lead 结合当前上下文直接回复。",
        "capabilities": ["tool.memory"],
        "required": True,
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "failed_at": None,
        "tool_evidence": [],
        "failure": None,
    }
    return {
        "prompt": prompt,
        "workspace_dir": workspace_dir,
        "strategy": "lead_direct_reply",
        "strategy_definition": {
            "id": "lead_direct_reply",
            "label": "Lead direct reply",
            "description": "轻量消息只由 Lead 处理，避免每次都走完整 Planner/Coder/Tester 流程。",
        },
        "agents": [lead.get("name") or "Lead"],
        "stages": [stage],
        "tasks": [
            {
                "id": "stage-01-lead_reply",
                "title": stage["title"],
                "description": stage["description"],
                "status": "pending",
                "owner": stage["owner"],
                "capabilities": stage["capabilities"],
                "dependencies": [],
            }
        ],
        "risks": [],
        "capabilities": ["tool.memory"],
        "tool_policy": {
            "mode": "approval_required",
            "recommended_tools": [],
            "requires_approval": ["bash", "write_file", "edit_file", "delete_file", "mcp_call"],
            "blocked_tools": [],
            "budgets": {"max_tool_calls": 4, "max_file_writes": 0, "max_test_runs": 0},
            "notes": ["轻量对话默认不执行文件修改和命令。"],
        },
        "skill_context": [],
        "mcp_plan": [],
        "summary": {
            "agent_count": len(team) or 1,
            "stage_count": 1,
            "capability_count": 1,
            "recommended_tool_count": 0,
            "skill_context_count": 0,
            "mcp_count": 0,
            "usable_mcp_count": 0,
            "risk_count": 0,
            "optional_stage_count": 0,
        },
    }


def _readonly_subagent_tools() -> list[dict[str, Any]]:
    allowed = {"read_file", "list_directory", "search_codebase", "project_context", "git_status", "git_diff"}
    return [tool for tool in TOOLS if tool.get("name") in allowed]


async def _run_readonly_subagent(
    prompt: str,
    system: str,
    agent_type: str,
    tools: list[dict[str, Any]] | None = None,
) -> str:
    return await run_subagent(
        prompt=prompt,
        system=system,
        agent_type=agent_type,
        tools=tools or _readonly_subagent_tools(),
    )


from src.agent.state import WorkflowCancelledError
from src.api.services.benchmark_service import emit_benchmark_run, list_benchmarks
from src.api.services.conversation_service import (
    create_conversation,
    compose_runtime_team_async,
    finalize_conversation_run,
    get_conversation,
    link_run_to_conversation,
    list_conversations,
    refresh_conversation_recommendation,
    update_conversation_team,
)
from src.api.services.intent_router import is_lead_direct_intent
from src.api.services.demo_run import DEMO_PROMPT, emit_demo_run, write_demo_artifacts
from src.api.services.event_store import get_event_store
from src.api.services.sse_broker import stream_events_push, patch_event_store_for_push, get_sse_broker
# Enable push-based SSE: all events are automatically broadcast to connected clients
patch_event_store_for_push()
from src.api.services.orchestration_service import build_execution_plan_async, build_runtime_instructions
from src.api.services.change_tracker import ChangeTracker
from src.api.services.parallel_agent_service import run_parallel_agent_briefing
from src.api.services.run_history import list_run_history_with_active
from src.api.services.run_context import RunContext
from src.api.services.tool_events import capability_trace_for_tool, derive_agenthub_events
from src.api.services.context_service import build_context_pack
from src.runtime.run_budget import RunBudget
from src.runtime.run_manager import RunManager
from src.runtime.run_state import RunStatus, TERMINAL_STATUSES
from src.runtime.tool_policy_runtime import ToolPolicyRuntime
from src.api.services.approval_service import (
    create_tool_approval,
    wait_for_approval_async,
)
from src.api.services.workspace_registry_service import list_recent_projects

# Persistent metrics history file (project root, preserved across workspaces)
METRICS_HISTORY_FILE = os.path.join(ROOT, "metrics_history.json")

# Initialize SQLite database
from src.infra.db import init_db
init_db()

# ============================================================
# Active run management
# ============================================================

run_manager = RunManager()
active_runs = run_manager._active
runs_lock = run_manager._lock
event_store = get_event_store()


def _finalize_conversation_for_run(
    thread_id: str,
    workspace_dir: str,
    status: str,
    summary: str = "",
    error: str = "",
) -> None:
    """Sync terminal run status back to its owning conversation."""
    with runs_lock:
        run_info = active_runs.get(thread_id) or {}
        conversation_id = run_info.get("conversation_id")
    if not conversation_id:
        return
    finalize_conversation_run(
        conversation_id=conversation_id,
        thread_id=thread_id,
        status=status,
        workspace_dir=workspace_dir,
        summary=summary,
        error=error,
    )
    event_store.update_session(
        thread_id,
        workspace_dir,
        conversation_id=conversation_id,
        conversation_status=status,
    )


def _emit_agenthub_event(
    thread_id: str,
    event_type: str,
    title: str = "",
    content: str = "",
    agent: str = "lead",
    payload: dict[str, Any] | None = None,
    legacy_event: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
) -> AgentEvent:
    """Persist a unified nanoCursor event and optionally publish a legacy SSE event."""
    if workspace_dir is None:
        with runs_lock:
            run_info = active_runs.get(thread_id)
            workspace_dir = run_info.get("workspace_dir") if run_info else None
    workspace_dir = workspace_dir or _get_workspace()
    event = event_store.append_event(
        thread_id=thread_id,
        event_type=event_type,
        title=title,
        content=content,
        agent=agent,
        payload=payload or {},
        workspace_dir=workspace_dir,
    )

    if legacy_event is not None:
        with runs_lock:
            run_info = active_runs.get(thread_id)
            q = run_info.get("queue") if run_info else None
        if q:
            enriched = dict(legacy_event)
            enriched["agenthub_event"] = event.model_dump()
            q.put(json.dumps(enriched, ensure_ascii=False))

    return event


def _emit_agent_activity(
    *,
    thread_id: str,
    agent: str = "lead",
    title: str,
    content: str = "",
    workspace_dir: str | None = None,
    payload: dict[str, Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> AgentEvent:
    """Emit a user-facing progress heartbeat for perceptible runs."""
    merged = dict(payload or {})
    if input_tokens or output_tokens:
        merged["input_tokens"] = input_tokens
        merged["output_tokens"] = output_tokens
    return _emit_agenthub_event(
        thread_id=thread_id,
        event_type="agent_activity",
        title=title,
        content=content,
        agent=agent,
        payload=merged,
        workspace_dir=workspace_dir,
    )


def _sync_run_context(thread_id: str, workspace_dir: str) -> RunContext | None:
    """Persist the current in-memory run context into the session file."""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        metadata = run_info.session_metadata() if run_info else None
    if not run_info or not metadata:
        return run_info
    event_store.update_session(thread_id, workspace_dir, **metadata)
    # R3: also persist lifecycle stages as step records
    try:
        from src.api.services.run_ledger_service import sync_steps_from_lifecycle
        sync_steps_from_lifecycle(thread_id, metadata, workspace_dir)
    except Exception:
        pass
    return run_info


def _transition_runtime_state(thread_id: str, workspace_dir: str, status: RunStatus) -> None:
    """Best-effort sync between RunManager state and the durable run session."""
    sm = run_manager.get_state_machine(thread_id)
    if sm and sm.status != status and sm.can_transition(status):
        run_manager.transition(thread_id, status)

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if run_info:
            run_info.set_status(status.value)
    _sync_run_context(thread_id, workspace_dir)


def _emit_stage_updates(
    thread_id: str,
    workspace_dir: str,
    updates: list[dict[str, Any]] | None,
) -> None:
    for update in updates or []:
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="stage_updated",
            title=f"阶段状态：{update.get('title') or update.get('stage_id')}",
            content=f"{update.get('previous_status')} -> {update.get('status')}",
            agent=str(update.get("owner") or "lead").lower(),
            payload=update,
            workspace_dir=workspace_dir,
        )


# ============================================================
# API Rate limiting
# ============================================================

import time as _time

_workflow_start_times: dict[str, list[float]] = {}
_WORKFLOW_MIN_INTERVAL_SECONDS = 10


def _check_rate_limit(thread_id: str) -> tuple[bool, str]:
    now = _time.time()

    with runs_lock:
        run_info = active_runs.get(thread_id)
    if run_info and run_info.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
        return False, f"线程 {thread_id} 已有一个工作流在运行中，请等待完成后再试"

    last_times = _workflow_start_times.get(thread_id, [])
    recent = [t for t in last_times if now - t < _WORKFLOW_MIN_INTERVAL_SECONDS]
    if recent:
        wait_time = int(_WORKFLOW_MIN_INTERVAL_SECONDS - (now - max(recent)))
        return False, f"工作流启动过于频繁，请等待 {wait_time} 秒后再试"

    _workflow_start_times.setdefault(thread_id, []).append(now)
    if len(_workflow_start_times[thread_id]) > 10:
        _workflow_start_times[thread_id] = _workflow_start_times[thread_id][-10:]

    return True, ""


def _run_workflow(thread_id: str, initial_messages: list, workspace_dir: str, max_retries: int = 3, max_coder_steps: int = 15):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_workflow_async(thread_id, initial_messages, max_retries, max_coder_steps, workspace_dir)
        )
    finally:
        loop.close()
        try:
            run_manager.unregister(thread_id)
        except Exception:
            pass


def _run_workflow_from_messages(thread_id: str, messages: list, system: str, workspace_dir: str):
    """Resume a run with pre-built messages and system prompt."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_workflow_async_from_messages(thread_id, messages, system, workspace_dir)
        )
    finally:
        loop.close()
        try:
            run_manager.unregister(thread_id)
        except Exception:
            pass


def _demo_event_delay() -> float:
    try:
        return max(0.0, min(float(os.getenv("NANOCURSOR_DEMO_EVENT_DELAY", "0.08")), 2.0))
    except ValueError:
        return 0.08


def _run_demo_workflow(thread_id: str, workspace_dir: str, artifacts: dict[str, Any] | None = None) -> None:
    final_status = "completed"

    def update_status(status: str) -> None:
        nonlocal final_status
        final_status = status
        with runs_lock:
            run_info = active_runs.get(thread_id)
            if run_info:
                run_info.set_status(status)

    try:
        emit_demo_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            store=event_store,
            delay=_demo_event_delay(),
            status_callback=update_status,
            artifacts=artifacts,
        )
    except Exception as exc:
        final_status = "failed"
        event_store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="Demo Run 异常",
            content=str(exc),
            agent="lead",
            payload={"error": str(exc)},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(thread_id, workspace_dir, status="failed", error=str(exc))
    finally:
        with runs_lock:
            run_info = active_runs.get(thread_id)
            if run_info:
                run_info.finalize_lifecycle(final_status)
                run_info.set_status(final_status)
                event_store.update_session(
                    thread_id,
                    workspace_dir,
                    status=final_status,
                    **run_info.session_metadata(),
                )
        try:
            from src.api.services.delivery_service import finalize_delivery as _finalize_delivery
            _finalize_delivery(thread_id, workspace_dir)
        except Exception:
            pass
        try:
            run_manager.finalize(thread_id, final_status)
        finally:
            run_manager.unregister(thread_id)


async def _run_workflow_async(thread_id: str, initial_messages: list, max_retries: int, max_coder_steps: int, workspace_dir: str | None = None):
    """Async internal implementation of _run_workflow."""

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if not run_info:
            return
        q = run_info["queue"]
        workspace_dir = workspace_dir or run_info.get("workspace_dir")
        execution_plan = run_info.get("execution_plan", {})
        run_team = run_info.get("team", [])
        conversation_id = run_info.get("conversation_id")

        tool_policy_data = execution_plan.get("tool_policy", {})
        if isinstance(tool_policy_data, dict):
            policy_runtime = ToolPolicyRuntime(
                policy=tool_policy_data,
                budget=RunBudget(
                    max_tool_calls=tool_policy_data.get("budgets", {}).get("max_tool_calls", 40),
                    max_file_writes=tool_policy_data.get("budgets", {}).get("max_file_writes", 8),
                    max_test_runs=tool_policy_data.get("budgets", {}).get("max_test_runs", 3),
                ),
            )
        else:
            policy_runtime = ToolPolicyRuntime()
        change_tracker = ChangeTracker(thread_id, workspace_dir or _get_workspace())
        current_run = active_runs.get(thread_id)
        if current_run:
            current_run["change_tracker"] = change_tracker
    workspace_dir = workspace_dir or _get_workspace()

    messages = [{"role": m.type if hasattr(m, 'type') else 'user', "content": m.content} for m in initial_messages]

    _wd = str(get_workdir())
    strategy = execution_plan.get("strategy", "feature_delivery")
    from src.agent.prompt_builder import _build_core
    system = _build_core(strategy)
    system = f"{system}\n\n注意：工作目录已经是 {_wd}，写文件名时直接用文件名，不要加 workspace/ 前缀。"
    runtime_instructions = build_runtime_instructions(execution_plan, run_team)
    if runtime_instructions:
        system = f"{system}\n{runtime_instructions}"
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="orchestration_applied",
            title="动态编排已注入 Runtime",
            content="本次运行将按团队执行策略约束 Agent 的阶段、能力和验证要求。",
            agent="lead",
            payload={
                "strategy": execution_plan.get("strategy"),
                "stage_count": len(execution_plan.get("stages", [])),
                "team_count": len(run_team),
                "runtime_instruction_length": len(runtime_instructions),
            },
            workspace_dir=workspace_dir,
        )
    _emit_agent_activity(
        thread_id=thread_id,
        agent="lead",
        title="Lead 正在判断任务复杂度",
        content="正在结合执行策略、团队配置、上下文包和工具权限决定本轮怎么推进。",
        workspace_dir=workspace_dir,
        payload={
            "phase": "complexity_assessment",
            "strategy": execution_plan.get("strategy"),
            "complexity": execution_plan.get("complexity", {}),
        },
    )
    try:
        context_pack = build_context_pack(
            prompt=str(messages[-1].get("content", "")) if messages else "",
            team=run_team,
            workspace_dir=workspace_dir,
            execution_plan=execution_plan,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        system = f"{system}\n\n{context_pack.to_text()}"
        event_store.update_session(
            thread_id,
            workspace_dir,
            context_pack=context_pack.to_dict(),
        )
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="context_pack_built",
            title="上下文包已构建",
            content="已注入会话摘要、运行摘要、相关文件、最近变更、文件大纲和当前计划。",
            agent="system",
            payload={
                "relevant_files": context_pack.relevant_files,
                "recent_changes": context_pack.recent_changes,
                "file_outline_count": len(context_pack.file_outlines),
                "estimated_tokens": context_pack.estimate_tokens(),
            },
            workspace_dir=workspace_dir,
        )
        _emit_agent_activity(
            thread_id=thread_id,
            agent="lead",
            title="Lead 已压缩上下文",
            content=f"已选择 {len(context_pack.relevant_files)} 个相关文件、{len(context_pack.file_outlines)} 个文件大纲和最近变更。",
            workspace_dir=workspace_dir,
            payload={
                "phase": "context_pack",
                "relevant_files": context_pack.relevant_files,
                "file_outline_count": len(context_pack.file_outlines),
            },
        )
    except Exception as exc:
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="context_pack_failed",
            title="上下文包构建失败",
            content=str(exc),
            agent="system",
            payload={"error": str(exc)},
            workspace_dir=workspace_dir,
        )

    pending_policy_decisions = []
    approved_tools_for_run: set[str] = set()
    run_input_tokens = 0
    run_output_tokens = 0

    def _approval_wait_should_abort() -> bool:
        return _should_cancel_run(thread_id)

    async def on_tool_check(tool_name: str, tool_input: dict):
        decision = policy_runtime.check(tool_name, tool_input)
        trace = capability_trace_for_tool(tool_name)
        target = (
            tool_input.get("path")
            or tool_input.get("filename")
            or tool_input.get("command")
            or tool_input.get("query")
            or ""
        ) if isinstance(tool_input, dict) else ""
        action_text = f"准备调用 {tool_name}"
        if target:
            action_text = f"{action_text}: {str(target)[:120]}"
        _emit_agent_activity(
            thread_id=thread_id,
            agent=str(trace.get("agent") or "lead"),
            title=f"{trace.get('agent') or 'Agent'} 正在准备工具调用",
            content=action_text,
            workspace_dir=workspace_dir,
            payload={
                "phase": "tool_check",
                "tool": tool_name,
                "target": target,
                "decision": decision.to_dict(),
                "capability_trace": trace,
            },
        )
        if decision.allowed and decision.requires_approval and tool_name in approved_tools_for_run:
            decision.requires_approval = False
            decision.status = "auto_allowed"
            decision.reason = f"{tool_name} 已在本次运行中批准，后续同类调用自动放行。"
        pending_policy_decisions.append(decision)
        if not decision.allowed:
            _emit_agenthub_event(
                thread_id=thread_id, event_type="tool_policy_blocked",
                title=f"工具被策略拦截: {tool_name}",
                content=decision.reason,
                agent="system",
                payload={"tool": tool_name, "decision": decision.to_dict()},
                workspace_dir=workspace_dir,
            )
            return decision

        if decision.requires_approval:
            # Persist the pending approval so the frontend can pick it up
            approval_timeout_seconds = 120.0
            create_tool_approval(
                thread_id,
                decision,
                workspace_dir,
                timeout_seconds=approval_timeout_seconds,
            )
            _transition_runtime_state(thread_id, workspace_dir, RunStatus.WAITING_APPROVAL)

            # Emit both approval_required and run_waiting_approval events
            _emit_agenthub_event(
                thread_id=thread_id, event_type="tool_approval_required",
                title=f"工具需要审批: {tool_name}",
                content=decision.reason,
                agent="system",
                payload={"tool": tool_name, "decision": decision.to_dict()},
                workspace_dir=workspace_dir,
            )
            _emit_agent_activity(
                thread_id=thread_id,
                agent="system",
                title="等待用户批准高风险工具",
                content=decision.reason,
                workspace_dir=workspace_dir,
                payload={
                    "phase": "approval_wait",
                    "tool": tool_name,
                    "decision": decision.to_dict(),
                    "can_cancel": True,
                },
            )
            _emit_agenthub_event(
                thread_id=thread_id, event_type="run_waiting_approval",
                title="等待用户审批",
                content=f"等待审批工具: {tool_name}",
                agent="system",
                payload={
                    "tool": tool_name,
                    "decision": decision.to_dict(),
                    "timeout_seconds": approval_timeout_seconds,
                },
                workspace_dir=workspace_dir,
            )

            # Wait without blocking this run's event loop.
            resolved = await wait_for_approval_async(
                thread_id, decision,
                timeout_seconds=approval_timeout_seconds,
                workspace_dir=workspace_dir,
                should_abort=_approval_wait_should_abort,
            )

            if resolved.get("status") == "approved":
                decision.allowed = True
                decision.status = "approved"
                approved_tools_for_run.add(tool_name)
            else:
                decision.allowed = False
                decision.status = "rejected"
                decision.reason = resolved.get("reason", "用户拒绝执行该工具。")
            _transition_runtime_state(thread_id, workspace_dir, RunStatus.RUNNING)

            _emit_agenthub_event(
                thread_id=thread_id, event_type="approval_resolved",
                title=f"审批结果: {decision.status}",
                content=resolved.get("comment", ""),
                agent="system",
                payload={"tool": tool_name, "decision": decision.to_dict(), "resolved": resolved},
                workspace_dir=workspace_dir,
            )

        return decision

    def on_tool_call(tool_name: str, tool_input: dict, output: str):
        decision = pending_policy_decisions.pop(0) if pending_policy_decisions else policy_runtime.check(tool_name, tool_input)
        if not decision.allowed:
            return
        ok_flag = not str(output or "").startswith("Error:")
        adaptation = policy_runtime.record(tool_name, ok=ok_flag)
        capability_trace = capability_trace_for_tool(tool_name)
        # B6: Emit adaptation events
        if adaptation:
            _emit_agenthub_event(
                thread_id=thread_id,
                event_type="tool_policy_adapted",
                title=f"策略自适应: {adaptation.get('type', '')}",
                content=adaptation.get("reason", ""),
                agent="system",
                payload={**adaptation, "budget": policy_runtime.budget.to_dict()},
                workspace_dir=workspace_dir,
            )
        # B5: Record file changes for cross-agent awareness
        if tool_name in ("write_file", "edit_file"):
            file_path = (tool_input or {}).get("file_path") or (tool_input or {}).get("path")
            if file_path:
                change_tracker.record_change(str(file_path), capability_trace["agent"], "modify")
        _emit_agenthub_event(
            thread_id=thread_id, event_type="tool_policy_checked",
            title=f"策略检查: {tool_name}",
            content=decision.reason,
            agent="system",
            payload={"tool": tool_name, "decision": decision.to_dict(), "budget": policy_runtime.budget.to_dict()},
            workspace_dir=workspace_dir,
        )
        with runs_lock:
            current_run = active_runs.get(thread_id)
            stage_updates = (
                current_run.apply_tool_event(
                    tool_name=tool_name,
                    capability_id=capability_trace["capability_id"],
                    agent=capability_trace["agent"],
                    ok=not str(output or "").startswith("Error:"),
                    output=output or "",
                )
                if current_run
                else []
            )
            current_stage_id = (
                current_run.metadata.get("lifecycle", {}).get("current_stage_id")
                if current_run
                else None
            )
        _emit_agent_activity(
            thread_id=thread_id,
            agent=capability_trace["agent"].lower(),
            title=f"{capability_trace['agent']} 完成工具调用",
            content=(output or "")[:240],
            workspace_dir=workspace_dir,
            payload={
                "phase": "tool_finished",
                "tool": tool_name,
                "ok": not str(output or "").startswith("Error:"),
                "stage_id": current_stage_id,
                "capability_trace": capability_trace,
            },
            input_tokens=run_input_tokens,
            output_tokens=run_output_tokens,
        )
        _sync_run_context(thread_id, workspace_dir)
        _emit_stage_updates(thread_id, workspace_dir, stage_updates)

        # R3: persist tool call to ledger
        call_id = ""
        try:
            from src.api.services.run_ledger_service import record_tool_call_start, record_tool_call_finish
            ok_flag = not str(output or "").startswith("Error:")
            rec = record_tool_call_start(
                thread_id=thread_id, tool_name=tool_name, tool_input=tool_input,
                step_id=current_stage_id or "", workspace_dir=workspace_dir,
            )
            call_id = rec.call_id
            record_tool_call_finish(
                call_id=call_id, thread_id=thread_id, output=output, ok=ok_flag,
                workspace_dir=workspace_dir,
            )
        except Exception:
            pass

        legacy_event = {
            "type": "tool_call",
            "tool": tool_name,
            "input": tool_input,
            "output": output[:500] if output else "",
            "metrics": metrics_collector.dump_summary(),
        }
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="tool_call_finished",
            title=f"能力调用：{capability_trace['capability_name']}",
            content=output[:1000] if output else "",
            agent=capability_trace["agent"].lower(),
            payload={
                "tool": tool_name,
                "input": tool_input,
                "output": output[:5000] if output else "",
                "metrics": metrics_collector.dump_summary(),
                "capability_trace": capability_trace,
                "stage_id": current_stage_id,
            },
            legacy_event=legacy_event,
            workspace_dir=workspace_dir,
        )
        for derived_event in derive_agenthub_events(
            tool_name=tool_name,
            tool_input=tool_input,
            output=output,
            workspace_dir=workspace_dir,
            thread_id=thread_id,
        ):
            _emit_agenthub_event(thread_id=thread_id, workspace_dir=workspace_dir, **derived_event)

    final_status = "completed"

    try:
        if _should_cancel_run(thread_id):
            raise WorkflowCancelledError("Agent 运行已取消")
        _emit_agent_activity(
            thread_id=thread_id,
            agent="lead",
            title="Lead 正在创建临时只读分析",
            content="复杂任务会先让临时子 Agent 做只读预分析，完成后自动合并并归档。",
            workspace_dir=workspace_dir,
            payload={"phase": "parallel_briefing"},
        )
        parallel_result = await run_parallel_agent_briefing(
            thread_id=thread_id,
            prompt=str(messages[-1].get("content", "")) if messages else "",
            workspace_dir=workspace_dir,
            execution_plan=execution_plan,
            runner=_run_readonly_subagent,
            emit_event=_emit_agenthub_event,
            tools=_readonly_subagent_tools(),
        )
        parallel_briefing = parallel_result.get("briefing") if isinstance(parallel_result, dict) else ""
        merge_guidance = parallel_result.get("merge_guidance") if isinstance(parallel_result, dict) else ""
        parallel_context = "\n\n".join(str(item) for item in [parallel_briefing, merge_guidance] if item)
        if parallel_context:
            messages.append({"role": "user", "content": parallel_context})
            _emit_agenthub_event(
                thread_id=thread_id,
                event_type="parallel_briefing_injected",
                title="并行预分析已注入 Lead 上下文",
                content="Lead 将结合临时子 Agent 的只读分析和合并策略继续执行主流程。",
                agent="lead",
                payload={
                    "contribution_count": len(
                        parallel_result.get("contributions", {}).get("contributions", [])
                        if isinstance(parallel_result.get("contributions"), dict)
                        else []
                    ),
                    "has_merge_guidance": bool(merge_guidance),
                },
                workspace_dir=workspace_dir,
            )
            _emit_agent_activity(
                thread_id=thread_id,
                agent="lead",
                title="Lead 已合并临时 Agent 预分析",
                content="只读预分析已经合并进主上下文，接下来进入主 Agent 循环。",
                workspace_dir=workspace_dir,
                payload={
                    "phase": "parallel_briefing_merged",
                    "has_merge_guidance": bool(merge_guidance),
                },
            )

        if _should_cancel_run(thread_id):
            raise WorkflowCancelledError("Agent 运行已取消")

        def _on_llm_response(input_tokens: int, output_tokens: int):
            nonlocal run_input_tokens, run_output_tokens
            run_input_tokens += input_tokens
            run_output_tokens += output_tokens
            _emit_agenthub_event(
                thread_id=thread_id,
                event_type="metrics_updated",
                title="Token 指标更新",
                content=f"in={run_input_tokens} out={run_output_tokens}",
                agent="lead",
                payload={
                    "total_input_tokens": run_input_tokens,
                    "total_output_tokens": run_output_tokens,
                    "total_tokens": run_input_tokens + run_output_tokens,
                },
                workspace_dir=workspace_dir,
            )

        _emit_agent_activity(
            thread_id=thread_id,
            agent="lead",
            title="Lead 进入主循环",
            content="正在根据任务复杂度决定直接回答、修改文件、运行检查或创建临时 Agent。",
            workspace_dir=workspace_dir,
            payload={"phase": "agent_loop", "can_cancel": True},
        )
        result = ""
        _token_broker = get_sse_broker()
        _token_counter = 0

        def _agent_pool_status_callback(handle, event):
            """Emit SSE events for agent pool status changes."""
            _emit_agenthub_event(
                thread_id=thread_id,
                event_type=f"agent_{event}",
                title=f"子 Agent {event}",
                content=f"{handle.name} ({handle.role}) {event}",
                agent=handle.name.lower().replace(" ", "_"),
                payload={
                    "agent_id": handle.agent_id,
                    "name": handle.name,
                    "role": handle.role,
                    "status": handle.status,
                    "event": event,
                    "result": (handle.result or "")[:500] if handle.result else None,
                    "error": handle.error,
                },
                workspace_dir=workspace_dir,
            )

        async for event_type, *event_data in agent_loop_stream(
            messages=messages,
            system=system,
            tools=TOOLS,
            max_turns=100,
            on_tool_check=on_tool_check,
            on_tool_call=on_tool_call,
            on_cancel_check=lambda: _should_cancel_run(thread_id),
            session_id=thread_id,
            runtime_context={
                "thread_id": thread_id,
                "workspace_dir": workspace_dir,
                "agent": "Lead",
                "file_lock": get_file_lock(thread_id),
                "pool_status_callback": _agent_pool_status_callback,
            },
        ):
            if event_type == "token":
                text = event_data[0]
                result += text
                _token_counter += 1
                # Publish lightweight token event directly to broker (no disk persistence)
                _token_broker.publish(thread_id, AgentEvent(
                    id=f"{thread_id}-tok-{_token_counter}",
                    thread_id=thread_id,
                    type="token",
                    timestamp=_time.time(),
                    agent="lead",
                    content=text,
                    payload={"delta": text},
                ))
            elif event_type == "metrics":
                inp, out = event_data[0], event_data[1]
                _on_llm_response(inp, out)
            elif event_type == "error":
                raise RuntimeError(event_data[0])
            # tool_start, tool_input, tool_result are handled by on_tool_call callback
        if result.lstrip().startswith("Error:"):
            raise RuntimeError(result.removeprefix("Error:").strip() or result)
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="assistant_message",
            title="Agent 回复",
            content=result[:5000],
            agent="lead",
            payload={"content": result},
            legacy_event={
                "type": "node_update",
                "node": "agent",
                "data": {"content": result[:1000]},
            },
            workspace_dir=workspace_dir,
        )
        with runs_lock:
            run_info = active_runs.get(thread_id)
            stage_updates = run_info.finalize_lifecycle("completed") if run_info else []
        _sync_run_context(thread_id, workspace_dir)
        _emit_stage_updates(thread_id, workspace_dir, stage_updates)
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="done",
            title="任务完成",
            content="Agent 运行已完成",
            agent="lead",
            payload={"status": "completed"},
            legacy_event={"type": "done", "status": "completed"},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(
            thread_id,
            workspace_dir,
            status="completed",
            summary=result[:2000],
            execution_summary=result[:1200],
            saved_messages=messages,
        )
        _finalize_conversation_for_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="completed",
            summary=result,
        )

    except WorkflowCancelledError:
        final_status = "cancelled"
        with runs_lock:
            run_info = active_runs.get(thread_id)
            stage_updates = run_info.finalize_lifecycle("cancelled", "Agent 运行已取消") if run_info else []
        _sync_run_context(thread_id, workspace_dir)
        _emit_stage_updates(thread_id, workspace_dir, stage_updates)
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="done",
            title="任务已取消",
            content="Agent 运行已取消",
            agent="lead",
            payload={"status": "cancelled"},
            legacy_event={"type": "done", "status": "cancelled"},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(
            thread_id,
            workspace_dir,
            status="cancelled",
            summary="Agent 运行已取消",
            execution_summary="Agent 运行已取消",
            saved_messages=messages,
        )
        _finalize_conversation_for_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="cancelled",
            summary="Agent 运行已取消",
        )
    except Exception as e:
        final_status = "failed"
        import traceback
        error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"[_run_workflow_async] 工作流异常: {error_detail}")
        with runs_lock:
            run_info = active_runs.get(thread_id)
            stage_updates = run_info.finalize_lifecycle("failed", str(e)) if run_info else []
        _sync_run_context(thread_id, workspace_dir)
        _emit_stage_updates(thread_id, workspace_dir, stage_updates)
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="error",
            title="运行异常",
            content=str(e),
            agent="lead",
            payload={"error": str(e), "detail": error_detail},
            legacy_event={"type": "error", "message": str(e)},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(
            thread_id,
            workspace_dir,
            status="failed",
            error=str(e),
            execution_summary=f"失败: {str(e)[:1000]}",
            saved_messages=messages,
        )
        # R4: classify and persist failures
        try:
            from src.api.services.failure_classifier_service import save_failures as _save_failures
            _save_failures(thread_id, workspace_dir)
        except Exception:
            pass
        _finalize_conversation_for_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="failed",
            error=str(e),
        )
    finally:
        try:
            metrics_collector.flush_to_file()
            metrics_collector.append_to_history(METRICS_HISTORY_FILE, tag=thread_id[:8])
        except Exception:
            pass
        with runs_lock:
            if thread_id in active_runs:
                active_runs[thread_id].set_status(final_status)
        # R1: generate delivery contract for every terminal run
        try:
            from src.api.services.delivery_service import finalize_delivery as _finalize_delivery
            _finalize_delivery(thread_id, workspace_dir)
        except Exception:
            pass
        run_manager.finalize(thread_id, final_status)
        run_manager.unregister(thread_id)
        # Clean up agent pool
        try:
            from src.agent.agent_pool import cleanup_pool
            cleanup_pool(thread_id)
        except Exception:
            pass
        # Clean up file lock
        try:
            cleanup_file_lock(thread_id)
        except Exception:
            pass


async def _run_workflow_async_from_messages(thread_id: str, messages: list, system: str, workspace_dir: str):
    """Resume a run with pre-built messages and system prompt. Simplified version of _run_workflow_async."""
    import time as _time_mod

    final_status = "completed"
    _token_broker = get_sse_broker()
    _token_counter = 0

    def _on_llm_response(input_tokens: int, output_tokens: int):
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="metrics_updated",
            title="Token 指标更新",
            content=f"in={input_tokens} out={output_tokens}",
            agent="lead",
            payload={"total_input_tokens": input_tokens, "total_output_tokens": output_tokens},
            workspace_dir=workspace_dir,
        )

    try:
        result = ""

        def _agent_pool_status_callback(handle, event):
            """Emit SSE events for agent pool status changes."""
            _emit_agenthub_event(
                thread_id=thread_id,
                event_type=f"agent_{event}",
                title=f"子 Agent {event}",
                content=f"{handle.name} ({handle.role}) {event}",
                agent=handle.name.lower().replace(" ", "_"),
                payload={
                    "agent_id": handle.agent_id,
                    "name": handle.name,
                    "role": handle.role,
                    "status": handle.status,
                    "event": event,
                    "result": (handle.result or "")[:500] if handle.result else None,
                    "error": handle.error,
                },
                workspace_dir=workspace_dir,
            )

        async for event_type, *event_data in agent_loop_stream(
            messages=messages,
            system=system,
            tools=TOOLS,
            max_turns=100,
            on_cancel_check=lambda: _should_cancel_run(thread_id),
            session_id=thread_id,
            runtime_context={
                "thread_id": thread_id,
                "workspace_dir": workspace_dir,
                "agent": "Lead",
                "file_lock": get_file_lock(thread_id),
                "pool_status_callback": _agent_pool_status_callback,
            },
        ):
            if event_type == "token":
                text = event_data[0]
                result += text
                _token_counter += 1
                _token_broker.publish(thread_id, AgentEvent(
                    id=f"{thread_id}-tok-{_token_counter}",
                    thread_id=thread_id,
                    type="token",
                    timestamp=_time_mod.time(),
                    agent="lead",
                    content=text,
                    payload={"delta": text},
                ))
            elif event_type == "metrics":
                _on_llm_response(event_data[0], event_data[1])
            elif event_type == "error":
                raise RuntimeError(event_data[0])

        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="assistant_message",
            title="Agent 回复",
            content=result[:5000],
            agent="lead",
            payload={"content": result},
            workspace_dir=workspace_dir,
        )
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="done",
            title="任务完成",
            content="Agent 运行已完成",
            agent="lead",
            payload={"status": "completed"},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(
            thread_id, workspace_dir,
            status="completed",
            summary=result[:2000],
            execution_summary=result[:1200],
            saved_messages=messages,
        )

    except WorkflowCancelledError:
        final_status = "cancelled"
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="done",
            title="任务已取消",
            content="Agent 运行已取消",
            agent="lead",
            payload={"status": "cancelled"},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(
            thread_id, workspace_dir,
            status="cancelled",
            summary="Agent 运行已取消",
            saved_messages=messages,
        )

    except Exception as e:
        final_status = "failed"
        import traceback
        error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"[_run_workflow_async_from_messages] 工作流异常: {error_detail}")
        _emit_agenthub_event(
            thread_id=thread_id,
            event_type="error",
            title="运行异常",
            content=str(e),
            agent="lead",
            payload={"error": str(e), "detail": error_detail},
            workspace_dir=workspace_dir,
        )
        event_store.update_session(
            thread_id, workspace_dir,
            status="failed",
            error=str(e),
            saved_messages=messages,
        )

    finally:
        with runs_lock:
            if thread_id in active_runs:
                active_runs[thread_id].set_status(final_status)
        run_manager.finalize(thread_id, final_status)
        run_manager.unregister(thread_id)
        # Clean up file lock
        try:
            cleanup_file_lock(thread_id)
        except Exception:
            pass


def _extract_node_event(node_name: str, node_state: dict) -> dict:
    data = {}

    if node_name == "supervisor":
        data["last_action"] = node_state.get("last_action", "")
        data["current_task_id"] = node_state.get("current_task_id")
        data["step_budget"] = node_state.get("step_budget", 0)
        if node_state.get("task_pool"):
            data["task_pool"] = node_state["task_pool"]

    elif node_name == "planner":
        data["current_plan"] = node_state.get("current_plan", "")

    elif node_name == "coder":
        _extract_messages_content(node_state, data)

    elif node_name == "sandbox":
        data["error_trace"] = node_state.get("error_trace", "")
        data["retry_count"] = node_state.get("retry_count", 0)
        data["max_retries"] = node_state.get("max_retries", 3)

    elif node_name == "reviewer":
        _extract_messages_content(node_state, data)

    elif node_name == "verifier":
        data["verification_passed"] = node_state.get("verification_passed")
        data["verification_result"] = node_state.get("verification_result")
        _extract_messages_content(node_state, data)

    data["metrics"] = metrics_collector.dump_summary()
    return data


def _extract_messages_content(node_state: dict, data: dict) -> None:
    messages = node_state.get("messages")
    if not messages:
        return
    last_msg = messages[-1]
    content = last_msg.content
    if isinstance(content, list):
        text_parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        content = "\n".join(text_parts)
    if content and isinstance(content, str):
        data["content"] = content


# ============================================================
# API Routes
# ============================================================

@app.post("/api/run")
async def start_run(request: RunRequest):
    prompt = request.prompt
    thread_id = request.thread_id or str(uuid.uuid4())

    if request.workspace_dir:
        abs_path = _set_active_workspace(request.workspace_dir)
        print(f"[API] 设置工作区: {abs_path}")

    allowed, rate_limit_msg = _check_rate_limit(thread_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=rate_limit_msg)

    # Concurrency control
    with runs_lock:
        running_count = sum(1 for r in active_runs.values() if r.get("status") == "running")
    if running_count >= config_module.MAX_CONCURRENT_RUNS:
        raise HTTPException(
            status_code=429,
            detail=f"系统繁忙，当前已有 {running_count} 个运行中的任务（上限 {config_module.MAX_CONCURRENT_RUNS}）。请稍后再试。",
        )

    if request.messages:
        initial_messages = _messages_for_run(request.messages, prompt)
        print(f"[API] 使用前端传入历史消息 {len(request.messages)} 条，新 prompt 已追加")
    else:
        initial_messages = [HumanMessage(content=prompt)]
        print(f"[API] 开始新会话")

    print(f"[API] 构建 initial_messages 完成，共 {len(initial_messages)} 条消息")

    q = queue.Queue()
    run_workspace = _get_workspace()
    run_team = list(request.team or [])
    run_execution_plan = dict(request.execution_plan or {})

    with runs_lock:
        run_context = RunContext(
            thread_id=thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            conversation_id=request.conversation_id,
            team=run_team,
            execution_plan=run_execution_plan,
        )
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
    stage_updates = run_context.start_first_stage()
    _sync_run_context(thread_id, run_workspace)
    _emit_stage_updates(thread_id, run_workspace, stage_updates)
    _emit_agenthub_event(
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

    t = threading.Thread(
        target=_run_workflow,
        args=(thread_id, initial_messages, run_workspace),
        daemon=True,
    )
    active_runs[thread_id].thread = t
    t.start()

    return RunResponse(thread_id=thread_id, status="started")


@app.get("/api/run/{thread_id}/events")
async def stream_events(thread_id: str):
    run_info = active_runs.get(thread_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    q = run_info["queue"]

    def event_generator():
        while True:
            try:
                item = q.get(timeout=300)
                if item is None:
                    break
                event_type = json.loads(item).get("type", "message")
                yield f"event: {event_type}\ndata: {item}\n\n"
                if event_type in ("done", "error"):
                    break
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/runs")
async def start_agenthub_run(request: RunRequest):
    return await start_run(request)


@app.post("/api/conversations")
async def create_agenthub_conversation(request: ConversationCreateRequest):
    return {
        "conversation": create_conversation(
            prompt=request.prompt,
            workspace_dir=request.workspace_dir or _get_workspace(),
        )
    }


@app.get("/api/conversations")
async def list_agenthub_conversations(limit: int = 50, workspace_dir: str | None = None):
    safe_limit = min(max(limit, 0), 200)
    return {
        "conversations": list_conversations(
            limit=safe_limit,
            workspace_dir=workspace_dir or _get_workspace(),
        )
    }


@app.get("/api/conversations/{conversation_id}")
async def get_agenthub_conversation(conversation_id: str, workspace_dir: str | None = None):
    conv = get_conversation(conversation_id, workspace_dir or _get_workspace())
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@app.post("/api/conversations/{conversation_id}/team/recommend")
async def recommend_agenthub_conversation_team(conversation_id: str, request: ConversationTeamRecommendRequest | None = None):
    prompt = request.prompt if request else ""
    try:
        recommendation = refresh_conversation_recommendation(conversation_id, prompt, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "conversation_id": conversation_id,
        "recommendation": recommendation,
    }


@app.put("/api/conversations/{conversation_id}/team")
async def update_agenthub_conversation_team(conversation_id: str, request: ConversationTeamUpdateRequest):
    try:
        team = update_conversation_team(
            conversation_id=conversation_id,
            members=request.members,
            workspace_dir=request.workspace_dir or _get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"team": team}


@app.post("/api/conversations/{conversation_id}/runs")
async def create_agenthub_conversation_run(conversation_id: str, request: ConversationRunRequest):
    conversation = get_conversation(conversation_id, request.workspace_dir or _get_workspace())
    if not conversation:
        raise HTTPException(status_code=404, detail="未找到该会话")

    workspace_dir = request.workspace_dir or conversation["workspace_dir"]
    team = conversation.get("team", {})
    members = list(team.get("members", []))
    runtime_team_source = team.get("source", "conversation")
    is_simple = _is_simple_lead_message(request.prompt)
    runtime_composition = await compose_runtime_team_async(request.prompt, workspace_dir, conversation_id)
    if len(members) <= 1:
        members = list(runtime_composition.get("members", []))
        runtime_team_source = "runtime_composed"
    execution_plan = (
        _lead_only_execution_plan(request.prompt, workspace_dir, members)
        if is_simple
        else await build_execution_plan_async(
            prompt=request.prompt,
            team=members,
            workspace_dir=workspace_dir,
        )
    )
    execution_plan["complexity"] = runtime_composition.get("complexity", {})
    execution_plan.setdefault("summary", {})["runtime_team_source"] = runtime_team_source
    response = await start_run(
        RunRequest(
            prompt=request.prompt,
            workspace_dir=workspace_dir,
            messages=request.messages,
            conversation_id=conversation_id,
            team=members,
            execution_plan=execution_plan,
        )
    )
    thread_id = response.thread_id
    updated = link_run_to_conversation(
        conversation_id,
        thread_id,
        workspace_dir,
        prompt=request.prompt,
        team=members,
    )

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if run_info is not None:
            run_info.bind_conversation(conversation_id, members)
            run_info.set_execution_plan(execution_plan)

    event_store.update_session(
        thread_id,
        workspace_dir,
        conversation_id=conversation_id,
        team=members,
        execution_plan=execution_plan,
        agent_loop_policy=updated.get("agent_loop_policy", "run_per_message"),
        runtime_team_source=runtime_team_source,
        runtime_composition=runtime_composition,
    )
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="agent_complexity_assessed",
        title="Lead 已判断任务复杂度",
        content=runtime_composition.get("complexity", {}).get("rationale", ""),
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "complexity": runtime_composition.get("complexity", {}),
            "members": members,
            "source": runtime_team_source,
        },
        workspace_dir=workspace_dir,
    )
    _emit_agent_activity(
        thread_id=thread_id,
        agent="lead",
        title="Lead 已完成任务复杂度判断",
        content=runtime_composition.get("complexity", {}).get("rationale", ""),
        workspace_dir=workspace_dir,
        payload={
            "phase": "complexity_assessed",
            "complexity": runtime_composition.get("complexity", {}),
            "members": [member.get("name") for member in members],
        },
    )
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="team_updated",
        title="运行团队已绑定",
        content="Lead 已为本次运行准备 Agent 群组；默认会话团队仍保持轻量。",
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "members": members,
            "source": runtime_team_source,
        },
        workspace_dir=workspace_dir,
    )
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="plan_created",
        title="动态执行策略已生成",
        content="nanoCursor 已根据本会话团队生成本轮执行阶段。",
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "strategy": execution_plan["strategy"],
            "stages": execution_plan["stages"],
            "tasks": execution_plan["tasks"],
            "risks": execution_plan["risks"],
            "summary": execution_plan["summary"],
        },
        workspace_dir=workspace_dir,
    )
    return {
        "run": response,
        "conversation": updated,
        "runtime_team": {"members": members, "source": runtime_team_source},
    }


@app.post("/api/runs/demo")
async def start_demo_run(request: RunRequest | None = None):
    prompt = request.prompt if request and request.prompt else DEMO_PROMPT
    team = list(request.team) if request and request.team else []
    thread_id = str(uuid.uuid4())

    q = queue.Queue()
    run_workspace = request.workspace_dir if request and request.workspace_dir else _get_workspace()

    with runs_lock:
        run_context = RunContext(
            thread_id=thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            team=team,
        )
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=thread_id,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
        mode="demo",
    )
    session_metadata = run_context.session_metadata()
    if session_metadata:
        event_store.update_session(thread_id, run_workspace, **session_metadata)

    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Demo Run 已启动",
        content=prompt,
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": "demo"},
        workspace_dir=run_workspace,
    )

    artifacts = write_demo_artifacts(thread_id, run_workspace)
    t = threading.Thread(
        target=_run_demo_workflow,
        args=(thread_id, run_workspace, artifacts),
        daemon=True,
    )
    active_runs[thread_id].thread = t
    t.start()

    return RunResponse(thread_id=thread_id, status="started")


@app.get("/api/benchmarks")
async def get_agenthub_benchmarks():
    return {"benchmarks": list_benchmarks()}


@app.post("/api/benchmarks/run")
async def start_agenthub_benchmark_run(request: BenchmarkRunRequest):
    thread_id = str(uuid.uuid4())
    q = queue.Queue()
    run_workspace = request.workspace_dir or _get_workspace()

    with runs_lock:
        run_context = RunContext(
            thread_id=thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            team=request.team or [],
            execution_plan=dict(request.execution_plan or {}),
        )
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=thread_id,
        prompt=request.prompt,
        workspace_dir=run_workspace,
        status="running",
        mode=request.mode,
    )
    metrics_collector.reset()

    emitted = emit_benchmark_run(request, thread_id, event_store, run_workspace)
    if not emitted:
        raise HTTPException(status_code=400, detail="Benchmark 不存在")

    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Benchmark Run 已启动",
        content=request.prompt,
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": request.mode, "run_count": request.run_count},
        workspace_dir=run_workspace,
    )

    initial_messages = [HumanMessage(content=request.prompt)]
    t = threading.Thread(
        target=_run_workflow,
        args=(thread_id, initial_messages, run_workspace, request.run_count),
        daemon=True,
    )
    active_runs[thread_id].thread = t
    t.start()

    return RunResponse(thread_id=thread_id, status="started")


@app.get("/api/runs")
async def list_agenthub_runs():
    return {"runs": list_run_history_with_active(run_manager, _get_workspace())}


@app.get("/api/runs/active")
async def list_active_runs():
    return {"active_runs": run_manager.list_active()}


@app.get("/api/runs/{thread_id}")
async def get_agenthub_run_detail(thread_id: str):
    with runs_lock:
        run_info = active_runs.get(thread_id)
    if run_info:
        return {
            "thread_id": thread_id,
            "status": run_info.get("status"),
            "workspace_dir": run_info.get("workspace_dir", _get_workspace()),
            "conversation_id": run_info.get("conversation_id"),
        }
    session = _session_for_thread(thread_id)
    if session:
        return session
    raise HTTPException(status_code=404, detail="Run 不存在")


@app.get("/api/runs/{thread_id}/events/history")
async def get_agenthub_run_events_history(thread_id: str):
    events = event_store.list_events(thread_id, _workspace_for_thread(thread_id))
    return {"events": [event.model_dump() for event in events]}


# approvals → src/api/routes/approvals.py
# recovery/checkpoints/git/policy/observability/workspace-recovery → src/api/routes/recovery.py

@app.get("/api/runs/{thread_id}/events")
async def get_run_events_push(thread_id: str):
    workspace_dir = _workspace_for_thread(thread_id)
    return StreamingResponse(
        stream_events_push(thread_id, workspace_dir),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/runs/{thread_id}/cancel")
@app.post("/api/run/{thread_id}/cancel")
async def cancel_run(thread_id: str):
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
    workspace_dir = run_info.get("workspace_dir") or _workspace_for_thread(thread_id)
    _sync_run_context(thread_id, workspace_dir)
    event_store.update_session(thread_id, workspace_dir, status="cancelling")
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_cancelling",
        title="正在取消运行",
        content="用户请求取消运行，Agent 会在下一个安全检查点停止。",
        agent="lead",
        payload={"status": "cancelling"},
        workspace_dir=workspace_dir,
    )
    return CancelResponse(cancelled=True, thread_id=thread_id)


@app.get("/api/runs/{thread_id}/state")
async def get_run_state(thread_id: str):
    sm = run_manager.get_state_machine(thread_id)
    if not sm:
        raise HTTPException(status_code=404, detail=f"Run 不在活跃列表中: {thread_id}")
    return {"thread_id": thread_id, "state": sm.to_dict()}


@app.post("/api/runs/{thread_id}/resume")
async def resume_run(thread_id: str):
    sm = run_manager.get_state_machine(thread_id)
    if sm:
        raise HTTPException(status_code=409, detail="Run 已在活跃列表中。")

    session = _session_for_thread(thread_id)
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

    workspace_dir = session.get("workspace_dir") or _workspace_for_thread(thread_id)
    previous_status = session.get("status")

    # Register the run in the active manager
    q = queue.Queue()
    with runs_lock:
        run_context = RunContext(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            queue=q,
            status="running",
            conversation_id=session.get("conversation_id"),
        )
        active_runs[thread_id] = run_context
        run_manager.register(thread_id, workspace_dir)

    event_store.update_session(thread_id, workspace_dir, status="running")
    event_store.append_event(
        thread_id, "run_resumed",
        title="Run 已恢复",
        content=f"从 {previous_status} 状态恢复运行，加载了 {len(saved_messages)} 条历史消息。",
        agent="lead",
        payload={"previous_status": previous_status, "message_count": len(saved_messages)},
        workspace_dir=workspace_dir,
    )

    # Build system prompt
    execution_plan = session.get("execution_plan", {})
    run_team = session.get("team", [])
    strategy = execution_plan.get("strategy", "feature_delivery")
    from src.agent.prompt_builder import _build_core
    system = _build_core(strategy)
    system = f"{system}\n\n注意：工作目录已经是 {workspace_dir}，写文件名时直接用文件名，不要加 workspace/ 前缀。"
    runtime_instructions = build_runtime_instructions(execution_plan, run_team)
    if runtime_instructions:
        system = f"{system}\n{runtime_instructions}"

    # Start agent loop thread with saved messages
    t = threading.Thread(
        target=_run_workflow_from_messages,
        args=(thread_id, saved_messages, system, workspace_dir),
        daemon=True,
    )
    with runs_lock:
        active_runs[thread_id].thread = t
    t.start()

    return {"thread_id": thread_id, "status": "running", "ok": True, "resumed_from": previous_status}


@app.get("/api/runs/{thread_id}/lifecycle")
async def get_run_lifecycle(thread_id: str):
    """Get the full lifecycle status, history, and stage progress of a run."""
    sm = run_manager.get_state_machine(thread_id)
    session = _session_for_thread(thread_id)
    if not sm and not session:
        raise HTTPException(status_code=404, detail=f"Run 不存在: {thread_id}")

    session_status = str((session or {}).get("status", "unknown"))
    status = sm.status.value if sm else session_status
    is_terminal = sm.is_terminal() if sm else status in {item.value for item in TERMINAL_STATUSES}
    lifecycle = {
        "thread_id": thread_id,
        "status": status,
        "history": sm.history() if sm else [],
        "is_terminal": is_terminal,
        "session": session or {},
    }
    return lifecycle


@app.post("/api/runs/{thread_id}/retry")
async def retry_run(thread_id: str, request: RetryRunRequest | None = None):
    """Create a new run from a failed/cancelled previous run.

    The new run gets a fresh thread_id but copies the prompt, workspace_dir,
    and team from the original.  The original run is never overwritten.
    """
    session = _session_for_thread(thread_id)
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
    run_workspace = session.get("workspace_dir", _get_workspace())
    team = session.get("team", [])
    execution_plan = session.get("execution_plan", {})
    retry_context = _retry_context_for_run(thread_id, run_workspace, request.failure_id)
    prompt = _build_retry_prompt(
        original_prompt=original_prompt,
        original_thread_id=thread_id,
        original_status=original_status,
        retry_mode=retry_mode,
        retry_context=retry_context,
        instruction=request.instruction,
    )

    q = queue.Queue()
    with runs_lock:
        run_context = RunContext(
            thread_id=new_thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            mode="retry",
            team=team,
            execution_plan=execution_plan,
        )
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
    session_metadata = run_context.session_metadata()
    event_store.update_session(
        new_thread_id,
        run_workspace,
        **session_metadata,
        original_thread_id=thread_id,
        original_status=original_status,
        original_prompt=original_prompt,
        retry_mode=retry_mode,
        retry_context=retry_context,
    )
    stage_updates = run_context.start_first_stage()
    _sync_run_context(new_thread_id, run_workspace)
    _emit_stage_updates(new_thread_id, run_workspace, stage_updates)
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

    initial_messages = [HumanMessage(content=prompt)]
    t = threading.Thread(
        target=_run_workflow,
        args=(new_thread_id, initial_messages, run_workspace),
        daemon=True,
    )
    active_runs[new_thread_id].thread = t
    t.start()

    return {
        "original_thread_id": thread_id,
        "retry_thread_id": new_thread_id,
        "status": "created",
        "retry_mode": retry_mode,
    }


# /api/bash → src/api/routes/config.py

# Legacy/files/metrics/config/snapshots/backups/todos/memories/subagents/bash → src/api/routes/config.py + data.py

# ============================================================
# Static file serving (production)
# ============================================================

def serve_frontend(production: bool = False):
    if not production:
        return

    dist_dir = os.path.join(ROOT, "frontend", "dist")

    if os.path.exists(dist_dir):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_index(full_path: str):
            index_path = os.path.join(dist_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend 未构建")


# ============================================================
# Lifespan — startup / shutdown
# ============================================================

from fastapi import FastAPI
from contextlib import asynccontextmanager

ACTIVE_RUNS_STATE_FILE = os.path.join(config_module.PROJECT_ROOT, ".nanocursor", "active_runs_state.json")


def _save_active_runs_state():
    state_dir = os.path.dirname(ACTIVE_RUNS_STATE_FILE)
    os.makedirs(state_dir, exist_ok=True)
    with runs_lock:
        runs_snapshot = {}
        for tid, ctx in active_runs.items():
            runs_snapshot[tid] = {
                "thread_id": tid,
                "workspace_dir": ctx.get("workspace_dir", _get_workspace()),
                "status": ctx.get("status", "unknown"),
                "conversation_id": ctx.get("conversation_id", ""),
                "started_at": getattr(ctx, "started_at", 0) if hasattr(ctx, "started_at") else _time.time(),
                "mode": ctx.get("mode", "agenthub_delivery"),
            }
    try:
        with open(ACTIVE_RUNS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(runs_snapshot, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _recover_interrupted_runs():
    recovered = run_manager.detect_interrupted(_get_workspace())
    if recovered:
        for tid in recovered:
            event_store.append_event(
                thread_id=tid, event_type="error",
                title="运行中断", content="服务在运行期间关闭。该运行已标记为 interrupted，可重新启动。",
                agent="system", payload={"reason": "server_shutdown"},
                workspace_dir=_get_workspace(),
            )
        print(f"[startup] Recovered {len(recovered)} interrupted run(s)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _recover_interrupted_runs()

    # Background cleanup task
    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(600)  # every 10 minutes
            try:
                from src.api.services.run_lifecycle_service import cleanup_stale_runs
                cleaned = cleanup_stale_runs(run_manager, _get_workspace(), older_than_hours=24)
                if cleaned:
                    print(f"[cleanup] Cleaned {cleaned} stale run(s)")
            except Exception as exc:
                print(f"[cleanup] Error: {exc}")

    cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    cleanup_task.cancel()
    _save_active_runs_state()


app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  nanoCursor API Server")
    print("=" * 60)
    print(f"  工作区: {config_module.WORKSPACE_DIR}")
    print("  开发模式: 运行 'cd frontend && npm run dev'")
    print("  生产模式: 先 'npm run build'，再运行此脚本")
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8100)
