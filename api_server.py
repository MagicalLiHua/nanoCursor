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
from pydantic import BaseModel

from src.infra.messages import HumanMessage

# Import Pydantic API models
from src.api.models import (
    AgentEvent,
    AgentStateResponse,
    ArtifactCenterResponse,
    BackupContentResponse,
    BackupEntry,
    BackupListResponse,
    BenchmarkListResponse,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    CancelResponse,
    CapabilityRecommendRequest,
    CodeFile,
    ConfigResponse,
    ContextPackRequest,
    ConversationCreateRequest,
    ConversationRunRequest,
    ConversationTeamRecommendRequest,
    ConversationTeamUpdateRequest,
    EnvVar,
    FileContentResponse,
    FileEntry,
    FileListResponse,
    ApprovalDecisionRequest,
    GitCommitRequest,
    LLMProviderStatus,
    McpEnabledRequest,
    McpConfigResponse,
    McpPresetInstallRequest,
    McpServerUpsertRequest,
    McpToolCallRequest,
    McpValidateRequest,
    Message,
    PolicyDecisionRecordRequest,
    SkillDetailResponse,
    SkillUpdateRequest,
    MetricsCurrentResponse,
    MetricsLLMData,
    MemoryProfileResponse,
    MetricsRepairData,
    MetricsResponse,
    MetricsToolData,
    DeliveryScoreResponse,
    QualityGateResponse,
    PreferenceCreateRequest,
    RecoveryActionRequest,
    RecoveryActionResponse,
    RecoveryCenterResponse,
    RemediationRunRequest,
    RequirementTraceabilityResponse,
    WorkspaceHealth,
    WorkspaceIdentity,
    RollbackRequest,
    RollbackResponse,
    RunHistoryResponse,
    RunEventsResponse,
    RunRequest,
    RunResponse,
    RunSessionResponse,
    SkillImportRequest,
    SnapshotDetailResponse,
    SnapshotEntry,
    SnapshotListResponse,
    SnapshotMetadata,
    SystemConfig,
    TeamAgentCreateRequest,
    ToolApprovalResolveRequest,
)


class BashRequest(BaseModel):
    """Bash command execution request"""
    command: str
    workspace_dir: str | None = None
    timeout: int = 120


# Ensure project root is in sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Create app via factory (health/ready/version, CORS, middleware, error handlers included)
from src.api.app import create_app
app = create_app()

from src.agent.engine import agent_loop, run_subagent, TOOLS, get_workdir, MODEL
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


def _audit_route_action(
    *,
    thread_id: str,
    workspace_dir: str,
    kind: str,
    target: str = "",
    decision: str = "",
    result: str = "",
    reason: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit for route-level actions outside the action pipeline."""
    try:
        get_audit_repo().append(
            AuditRecord(
                audit_id=f"audit_{uuid.uuid4().hex[:12]}",
                thread_id=thread_id,
                action_id=f"route_{uuid.uuid4().hex[:12]}",
                kind=kind,
                target=target,
                decision=decision,
                result=result,
                reason=reason,
                detail=detail or {},
                created_at=_time.time(),
            ),
            workspace_dir,
        )
    except Exception:
        pass


from src.agent.state import WorkflowCancelledError
from src.api.services.agenthub_state import add_team_member, list_task_items, list_team_members
from src.api.services.artifact_service import build_artifact_center
from src.api.services.benchmark_service import emit_benchmark_run, get_benchmark, list_benchmarks
from src.api.services.capability_service import build_capability_hub, import_workspace_skill, recommend_capabilities
from src.api.services.mcp_service import (
    install_mcp_server_preset,
    list_mcp_server_presets,
    list_mcp_servers,
    upsert_mcp_server_config,
    validate_mcp_config,
)
from src.api.services.mcp_status_service import get_mcp_server_status, get_mcp_status, set_mcp_enabled, update_mcp_status
from src.api.services.mcp_runtime_service import probe_mcp_server, list_mcp_tools, call_mcp_tool
from src.api.services.skill_manifest_service import (
    list_skill_versions, parse_skill_manifest, restore_skill_version,
    save_skill_version, validate_skill_content,
)
from src.api.services.capability_usage_service import build_capability_usage
from src.api.services.skill_service import delete_workspace_skill, get_skill_detail, update_workspace_skill
from src.api.services.conversation_service import (
    create_conversation,
    finalize_conversation_run,
    get_conversation,
    link_run_to_conversation,
    list_conversations,
    refresh_conversation_recommendation,
    update_conversation_team,
)
from src.api.services.demo_run import DEMO_PROMPT, emit_demo_run, write_demo_artifacts
from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.api.services.sse_broker import get_sse_broker, stream_events_push, patch_event_store_for_push
# Enable push-based SSE: all events are automatically broadcast to connected clients
patch_event_store_for_push()
from src.api.services.orchestration_service import build_execution_plan, build_runtime_instructions
from src.api.services.quality_service import build_quality_gate
from src.api.services.preference_service import add_preference_memory, build_memory_profile
from src.api.services.recovery_action_service import execute_recovery_action
from src.api.services.recovery_service import build_recovery_center, rollback_from_backup
from src.api.services.report_service import build_delivery_report
from src.api.services.run_history import list_run_history_with_active
from src.api.services.run_context import RunContext
from src.api.services.score_service import build_delivery_score
from src.api.services.traceability_service import build_requirement_traceability
from src.api.services.tool_events import capability_trace_for_tool, derive_agenthub_events
from src.api.services.checkpoint_service import create_checkpoint, list_checkpoints, restore_checkpoint
from src.api.services.context_service import build_context_pack
from src.api.services.observability_service import build_run_observability, build_workspace_observability
from src.api.services.git_sandbox_service import commit_branch, discard_branch, git_branch_status, prepare_git_branch
from src.runtime.run_budget import RunBudget
from src.runtime.run_manager import RunManager
from src.runtime.run_state import RunStatus
from src.runtime.tool_policy_runtime import ToolPolicyRuntime
from src.runtime.audit_log import AuditRecord, get_audit_repo
from src.api.services.approval_service import (
    create_tool_approval,
    wait_for_approval_async,
    get_tool_approval,
    get_pending_approvals,
    resolve_tool_approval,
)
from src.api.services.workspace_registry_service import get_workspace_identity, list_recent_projects, open_project
from src.api.services.workspace_service import build_workspace_health, build_workspace_overview
from src.api.services.workspace_settings_service import (
    get_effective_settings, get_workspace_settings, save_workspace_settings, validate_settings,
)

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


def _approval_title(decision: str) -> str:
    labels = {
        "approved": "计划已批准",
        "revise": "计划需调整",
        "rejected": "计划已拒绝",
    }
    return labels.get(decision, "计划审批已记录")


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
    """Persist a unified AgentHub event and optionally publish a legacy SSE event."""
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
    if run_info and run_info.get("status") == "running":
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
    workspace_dir = workspace_dir or _get_workspace()

    messages = [{"role": m.type if hasattr(m, 'type') else 'user', "content": m.content} for m in initial_messages]

    _wd = str(get_workdir())
    system = f"""你是一个自动编程助手，在 {_wd} 工作目录。

【重要】你运行在 Windows 系统上！使用 Windows 命令：
- 用 `dir` 而不是 `ls`
- 用 `type` 而不是 `cat`
- 用 `del` 而不是 `rm`
- 用 `copy` 而不是 `cp`

你有以下工具：
- bash: 执行 shell 命令（参数：command）
- read_file: 读取文件（参数：path, limit 可选）
- write_file: 写文件（参数：path, content）
- edit_file: 编辑文件（参数：path, old_text, new_text）
- list_directory: 列出目录内容（参数：path）

注意：
- 工作目录已经是 {_wd}，所以写文件名时直接用文件名，不要加 workspace/ 前缀
- 例如：write_file(path="prime.py", content="...") 而不是 write_file(path="workspace/prime.py", content="...")
- 读文件同理，直接写文件名
"""
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

    pending_policy_decisions = []
    approved_tools_for_run: set[str] = set()

    def _approval_wait_should_abort() -> bool:
        sm = run_manager.get_state_machine(thread_id)
        if sm and sm.status in {
            RunStatus.CANCELLING,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
        }:
            return True
        with runs_lock:
            run_info = active_runs.get(thread_id)
            return bool(run_info and run_info.get("status") in {"cancelled", "failed", "interrupted"})

    async def on_tool_check(tool_name: str, tool_input: dict):
        decision = policy_runtime.check(tool_name)
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
        decision = pending_policy_decisions.pop(0) if pending_policy_decisions else policy_runtime.check(tool_name)
        if not decision.allowed:
            return
        policy_runtime.record(tool_name, ok=not str(output or "").startswith("Error:"))
        _emit_agenthub_event(
            thread_id=thread_id, event_type="tool_policy_checked",
            title=f"策略检查: {tool_name}",
            content=decision.reason,
            agent="system",
            payload={"tool": tool_name, "decision": decision.to_dict(), "budget": policy_runtime.budget.to_dict()},
            workspace_dir=workspace_dir,
        )

        capability_trace = capability_trace_for_tool(tool_name)
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
        result = await agent_loop(
            messages=messages,
            system=system,
            tools=TOOLS,
            max_turns=100,
            on_tool_check=on_tool_check,
            on_tool_call=on_tool_call,
        )
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
        event_store.update_session(thread_id, workspace_dir, status="completed")
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
        event_store.update_session(thread_id, workspace_dir, status="cancelled")
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
        event_store.update_session(thread_id, workspace_dir, status="failed", error=str(e))
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

    if request.messages:
        initial_messages = [HumanMessage(content=m.content) for m in request.messages]
        initial_messages.append(HumanMessage(content=prompt))
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
    execution_plan = build_execution_plan(
        prompt=request.prompt,
        team=team.get("members", []),
        workspace_dir=workspace_dir,
    )
    response = await start_run(
        RunRequest(
            prompt=request.prompt,
            workspace_dir=workspace_dir,
            conversation_id=conversation_id,
            team=team.get("members", []),
            execution_plan=execution_plan,
        )
    )
    thread_id = response.thread_id
    updated = link_run_to_conversation(
        conversation_id,
        thread_id,
        workspace_dir,
        prompt=request.prompt,
        team=team.get("members", []),
    )
    team = updated.get("team", {})

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if run_info is not None:
            run_info.bind_conversation(conversation_id, team.get("members", []))
            run_info.set_execution_plan(execution_plan)

    event_store.update_session(
        thread_id,
        workspace_dir,
        conversation_id=conversation_id,
        team=team.get("members", []),
        execution_plan=execution_plan,
        agent_loop_policy=updated.get("agent_loop_policy", "run_per_message"),
    )
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="team_updated",
        title="会话团队已绑定",
        content="本次运行将使用会话内的 Agent 群组配置。",
        agent="lead",
        payload={
            "conversation_id": conversation_id,
            "members": team.get("members", []),
            "source": team.get("source", "unknown"),
        },
        workspace_dir=workspace_dir,
    )
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="plan_created",
        title="动态执行策略已生成",
        content="AgentHub 已根据本会话团队生成本轮执行阶段。",
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
    return {"run": response, "conversation": updated}


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
    session = event_store.get_session(thread_id)
    if session:
        return session
    raise HTTPException(status_code=404, detail="Run 不存在")


@app.get("/api/runs/{thread_id}/events/history")
async def get_agenthub_run_events_history(thread_id: str):
    events = event_store.list_events(thread_id, _workspace_for_thread(thread_id))
    return {"events": [event.model_dump() for event in events]}


@app.post("/api/runs/{thread_id}/approval")
async def resolve_agenthub_approval(thread_id: str, decision: ApprovalDecisionRequest):
    title = _approval_title(decision.decision)
    _emit_agenthub_event(
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
        workspace_dir=_workspace_for_thread(thread_id),
    )
    return {"thread_id": thread_id, "plan_id": decision.plan_id, "decision": decision.decision}


@app.get("/api/runs/{thread_id}/approvals")
async def list_run_approvals(thread_id: str):
    """List all pending tool approval decisions for a run."""
    ws = _workspace_for_thread(thread_id)
    return {"approvals": get_pending_approvals(thread_id, ws)}


@app.get("/api/runs/{thread_id}/approvals/{decision_id}")
async def get_run_approval(thread_id: str, decision_id: str):
    """Get a single tool approval decision, including resolved/expired records."""
    ws = _workspace_for_thread(thread_id)
    result = get_tool_approval(thread_id, decision_id, ws)
    if not result:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    return result


@app.post("/api/runs/{thread_id}/approvals/{decision_id}")
async def resolve_run_approval(thread_id: str, decision_id: str, body: ToolApprovalResolveRequest):
    """Approve or reject a pending tool approval.

    Request body: {"approved": true, "comment": "允许执行"}
    """
    ws = _workspace_for_thread(thread_id)
    result = resolve_tool_approval(thread_id, decision_id, body.approved, body.comment, ws)
    if not result:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    return result


@app.get("/api/runs/{thread_id}/diff")
async def get_agenthub_run_diff(thread_id: str):
    return get_run_diff(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/report")
async def get_agenthub_run_report(thread_id: str):
    return build_delivery_report(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/quality")
async def get_agenthub_run_quality(thread_id: str):
    return build_quality_gate(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/score")
async def get_agenthub_run_score(thread_id: str):
    return build_delivery_score(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/traceability")
async def get_agenthub_run_traceability(thread_id: str):
    return build_requirement_traceability(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/capabilities")
async def get_agenthub_run_capabilities(thread_id: str):
    return build_capability_usage(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/artifacts")
async def get_agenthub_run_artifacts(thread_id: str):
    return build_artifact_center(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/recovery")
async def get_agenthub_run_recovery(thread_id: str):
    return build_recovery_center(thread_id, _workspace_for_thread(thread_id))


@app.post("/api/runs/{thread_id}/recovery/actions/{action_id}")
async def run_agenthub_recovery_action(thread_id: str, action_id: str, request: RecoveryActionRequest):
    workspace = _workspace_for_thread(thread_id)
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
        _audit_route_action(
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
        _audit_route_action(
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


@app.post("/api/runs/{thread_id}/remediation")
async def start_remediation_run(thread_id: str, request: RemediationRunRequest):
    sm = run_manager.get_state_machine(thread_id)
    if sm and not sm.current_status.endswith("ed"):
        raise HTTPException(status_code=409, detail="Run 还在活跃中，无法启动修复。")
    new_tid = str(uuid.uuid4())
    q = queue.Queue()
    run_workspace = _workspace_for_thread(thread_id)
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
    t = threading.Thread(
        target=_run_workflow,
        args=(new_tid, initial_messages, run_workspace),
        daemon=True,
    )
    active_runs[new_tid].thread = t
    t.start()

    return {"original_thread_id": thread_id, "retry_thread_id": new_tid, "status": "created"}


@app.post("/api/runs/{thread_id}/checkpoints")
async def create_run_checkpoint(thread_id: str, request: RecoveryActionRequest):
    workspace = _workspace_for_thread(thread_id)
    target_path = request.target_path or request.target
    if not target_path:
        _audit_route_action(
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
        _audit_route_action(
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
        _audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="checkpoint_create",
            target=target_path,
            decision="denied",
            result="failure",
            reason=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{thread_id}/checkpoints")
async def list_run_checkpoints(thread_id: str):
    return list_checkpoints(thread_id, _workspace_for_thread(thread_id))


@app.post("/api/runs/{thread_id}/checkpoints/{checkpoint_id}/restore")
async def restore_run_checkpoint(thread_id: str, checkpoint_id: str, request: RecoveryActionRequest):
    workspace = _workspace_for_thread(thread_id)
    try:
        result = restore_checkpoint(
            checkpoint_id=checkpoint_id,
            thread_id=thread_id,
            confirmed=request.confirmed,
            workspace_dir=workspace,
        )
        _audit_route_action(
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
        _audit_route_action(
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


@app.post("/api/runs/{thread_id}/git/prepare")
async def prepare_run_git_branch(thread_id: str):
    workspace = _workspace_for_thread(thread_id)
    result = prepare_git_branch(thread_id, workspace)
    _audit_route_action(
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


@app.get("/api/runs/{thread_id}/git/status")
async def get_run_git_status(thread_id: str):
    return git_branch_status(thread_id, _workspace_for_thread(thread_id))


@app.post("/api/runs/{thread_id}/git/commit")
async def commit_run_branch(thread_id: str, commit_request: GitCommitRequest):
    workspace = _workspace_for_thread(thread_id)
    result = commit_branch(thread_id, commit_request.message, workspace)
    _audit_route_action(
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


@app.post("/api/runs/{thread_id}/git/discard")
async def discard_run_branch(thread_id: str, request: RecoveryActionRequest):
    workspace = _workspace_for_thread(thread_id)
    try:
        result = discard_branch(
            thread_id,
            confirmed=request.confirmed,
            workspace_dir=workspace,
        )
        _audit_route_action(
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
        _audit_route_action(
            thread_id=thread_id,
            workspace_dir=workspace,
            kind="git_operation",
            target="discard",
            decision="confirmed" if request.confirmed else "denied",
            result="failure",
            reason=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{thread_id}/policy")
async def get_run_policy(thread_id: str):
    return {"thread_id": thread_id, "decisions": []}


@app.post("/api/runs/{thread_id}/policy/decision")
async def record_policy_decision(thread_id: str, request: PolicyDecisionRecordRequest):
    return {"thread_id": thread_id, "decision": request.decision, "status": "recorded"}


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


@app.get("/api/tasks")
async def list_team_tasks():
    return {"tasks": list_task_items()}


@app.get("/api/team")
async def list_team_agents():
    return {"team": list_team_members()}


@app.get("/api/capabilities")
async def get_agenthub_capabilities():
    return build_capability_hub(_get_workspace())


@app.post("/api/capabilities/recommend")
async def recommend_caps(request: CapabilityRecommendRequest):
    return recommend_capabilities(request.prompt, _get_workspace())


@app.post("/api/capabilities/skills")
async def create_skill(request: SkillImportRequest):
    skill = import_workspace_skill(request.name, request.description, request.content, _get_workspace())
    return {"skill": skill, "hub": build_capability_hub(_get_workspace()), "ok": True}


@app.get("/api/capabilities/mcp")
async def get_agenthub_mcp_servers():
    return {"mcp": list_mcp_servers(_get_workspace())}


@app.post("/api/capabilities/mcp/validate")
async def validate_mcp_config_route(request: McpValidateRequest):
    return validate_mcp_config(request.server_id, _get_workspace())


@app.post("/api/capabilities/mcp/servers")
async def upsert_mcp_server(request: McpServerUpsertRequest):
    if not request.command.strip():
        raise HTTPException(status_code=400, detail="MCP command 不能为空")
    result = upsert_mcp_server_config(
        request.server_id,
        request.command,
        request.args,
        request.env_keys,
        workspace_dir=_get_workspace(),
        enabled=request.enabled,
        ignored_env_keys=request.ignored_env_keys,
    )
    return result


@app.get("/api/capabilities/mcp/presets")
async def get_mcp_server_presets():
    return list_mcp_server_presets(_get_workspace())


@app.post("/api/capabilities/mcp/presets/{preset_id}/install")
async def install_mcp_server_preset_route(
    preset_id: str,
    request: McpPresetInstallRequest | None = None,
):
    try:
        return install_mcp_server_preset(
            preset_id,
            _get_workspace(),
            enabled=request.enabled if request else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/capabilities/skills/{skill_id}")
async def get_skill(skill_id: str):
    try:
        detail = get_skill_detail(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail


@app.put("/api/capabilities/skills/{skill_id}")
async def edit_skill(skill_id: str, request: SkillUpdateRequest):
    try:
        save_skill_version(skill_id, request.content, _get_workspace())
        detail = update_workspace_skill(skill_id, request.content, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail


@app.delete("/api/capabilities/skills/{skill_id}")
async def remove_skill(skill_id: str):
    try:
        result = delete_workspace_skill(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": bool(result.get("ok", True)), "skill_id": skill_id}


@app.post("/api/capabilities/skills/{skill_id}/validate")
async def validate_skill(skill_id: str):
    try:
        detail = get_skill_detail(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return validate_skill_content(detail.get("content", ""))


@app.get("/api/capabilities/skills/{skill_id}/versions")
async def get_skill_versions(skill_id: str):
    return list_skill_versions(skill_id, _get_workspace())


@app.post("/api/capabilities/skills/{skill_id}/versions/{version_id}/restore")
async def restore_skill_version_route(skill_id: str, version_id: str):
    try:
        return restore_skill_version(skill_id, version_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/capabilities/mcp/status")
async def get_mcp_route_status():
    return get_mcp_status(_get_workspace())


@app.get("/api/capabilities/mcp/{server_id}/status")
async def get_mcp_server_route_status(server_id: str):
    return get_mcp_server_status(server_id, _get_workspace())


@app.put("/api/capabilities/mcp/{server_id}/enabled")
async def set_mcp_enabled_route(server_id: str, data: McpEnabledRequest):
    return set_mcp_enabled(server_id, data.enabled, _get_workspace())


@app.post("/api/capabilities/mcp/{server_id}/probe")
async def probe_mcp_server_route(server_id: str):
    """Run diagnostics on an MCP server: command, env, config, enabled."""
    return probe_mcp_server(server_id, _get_workspace())


@app.get("/api/capabilities/mcp/{server_id}/tools")
async def list_mcp_tools_route(server_id: str, refresh: bool = False):
    """List tools exposed by an MCP server."""
    return list_mcp_tools(server_id, _get_workspace(), force_refresh=refresh)


@app.post("/api/capabilities/mcp/{server_id}/tools/{tool_name}/call")
async def call_mcp_tool_route(server_id: str, tool_name: str, request: McpToolCallRequest | None = None):
    """Call an MCP tool."""
    return call_mcp_tool(server_id, tool_name, request.arguments if request else {}, _get_workspace())


@app.post("/api/runs/context-pack")
async def build_run_context_pack_route(request: ContextPackRequest):
    return build_context_pack(request.objective, request.workspace_dir or _get_workspace())


@app.get("/api/runs/{thread_id}/context-pack")
async def get_run_context_pack_route(thread_id: str):
    return build_context_pack("", _workspace_for_thread(thread_id), thread_id)


@app.post("/api/team/agents")
async def create_team_agent(request: TeamAgentCreateRequest):
    try:
        agent = add_team_member(
            request.name,
            request.role,
            request.goal,
            request.tools,
            request.capabilities,
            _get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent": agent}


@app.get("/api/preferences/profile")
async def get_preference_profile():
    return build_memory_profile(_get_workspace())


@app.post("/api/preferences")
async def add_preference(request: PreferenceCreateRequest):
    try:
        mem = add_preference_memory(
            request.preference_type,
            request.content,
            request.importance,
            _get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"memory": mem}


@app.get("/api/recovery")
async def get_recovery_center():
    return build_recovery_center("", _get_workspace())


@app.post("/api/recovery/rollback")
async def rollback_recovery(request: RollbackRequest):
    workspace = _get_workspace()
    target = request.target_path
    if not request.confirmed:
        _audit_route_action(
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
        _audit_route_action(
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
        _audit_route_action(
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


@app.get("/api/runs/{thread_id}/observability")
async def get_run_observability(thread_id: str):
    return build_run_observability(thread_id, _workspace_for_thread(thread_id))


@app.post("/api/runs/{thread_id}/cancel")
@app.post("/api/run/{thread_id}/cancel")
async def cancel_run(thread_id: str):
    with runs_lock:
        run_info = active_runs.get(thread_id)

    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    if run_info.get("status") != "running":
        raise HTTPException(status_code=400, detail=f"工作流状态为 {run_info.get('status')}，无法取消")

    try:
        run_manager.request_cancel(thread_id)
    except ValueError:
        pass
    run_info.set_status("cancelled")
    workspace_dir = run_info.get("workspace_dir") or _workspace_for_thread(thread_id)
    event_store.update_session(thread_id, workspace_dir, status="cancelled")
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="done",
        title="任务已取消",
        content="用户请求取消运行",
        agent="lead",
        payload={"status": "cancelled"},
        workspace_dir=workspace_dir,
    )
    _finalize_conversation_for_run(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        status="cancelled",
        summary="用户请求取消运行",
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

    session = event_store.get_session(thread_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Run 不存在: {thread_id}")
    if session.get("status") != "interrupted":
        raise HTTPException(status_code=400, detail=f"只能恢复 interrupted 状态的 run，当前状态: {session.get('status')}")

    event_store.update_session(thread_id, status="recovering")
    event_store.append_event(
        thread_id, "run_resumed",
        title="Run 已恢复",
        content="从 interrupted 状态恢复运行。",
        agent="lead",
        payload={"previous_status": "interrupted"},
    )
    return {"thread_id": thread_id, "status": "recovering", "ok": True}


@app.get("/api/runs/{thread_id}/lifecycle")
async def get_run_lifecycle(thread_id: str):
    """Get the full lifecycle status, history, and stage progress of a run."""
    sm = run_manager.get_state_machine(thread_id)
    session = event_store.get_session(thread_id)
    if not sm and not session:
        raise HTTPException(status_code=404, detail=f"Run 不存在: {thread_id}")

    lifecycle = {
        "thread_id": thread_id,
        "status": sm.status.value if sm else session.get("status", "unknown"),
        "history": sm.history() if sm else [],
        "is_terminal": sm.is_terminal() if sm else False,
        "session": session or {},
    }
    return lifecycle


@app.post("/api/runs/{thread_id}/retry")
async def retry_run(thread_id: str):
    """Create a new run from a failed/cancelled previous run.

    The new run gets a fresh thread_id but copies the prompt, workspace_dir,
    and team from the original.  The original run is never overwritten.
    """
    session = event_store.get_session(thread_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"原 run 不存在: {thread_id}")

    original_status = session.get("status", "")
    if original_status not in ("failed", "cancelled", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"只能重试已结束的 run，当前状态: {original_status}",
        )

    new_thread_id = str(uuid.uuid4())
    prompt = session.get("prompt", "")
    run_workspace = session.get("workspace_dir", _get_workspace())
    team = session.get("team", [])

    q = queue.Queue()
    with runs_lock:
        run_context = RunContext(
            thread_id=new_thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            team=team,
            execution_plan=session.get("execution_plan", {}),
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
        mode="normal",
    )
    event_store.append_event(
        thread_id=new_thread_id,
        event_type="run_retried",
        title="Run 已重试",
        content=prompt,
        agent="lead",
        payload={"original_thread_id": thread_id, "original_status": original_status},
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
    }


@app.post("/api/bash")
async def run_bash_command(request: BashRequest):
    import subprocess as sp

    command = request.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="命令不能为空")

    work_dir = request.workspace_dir or config_module.WORKSPACE_DIR
    work_dir = os.path.abspath(work_dir)

    dangerous = ["rm -rf /", "sudo ", "shutdown", "reboot", "> /dev/", "mkfs", "chroot", "dd if="]
    for pattern in dangerous:
        if pattern in command:
            return {"success": False, "stdout": "", "stderr": f"Error: Dangerous command blocked (matches '{pattern}')", "exit_code": -1}

    timeout = min(request.timeout, 300)

    try:
        r = sp.run(
            command, shell=True, cwd=work_dir,
            capture_output=True, timeout=timeout,
        )
        try:
            stdout = r.stdout.decode('gbk', errors='replace')
            stderr = r.stderr.decode('gbk', errors='replace')
        except Exception:
            stdout = r.stdout.decode('utf-8', errors='replace') if r.stdout else ""
            stderr = r.stderr.decode('utf-8', errors='replace') if r.stderr else ""

        return {
            "success": r.returncode == 0,
            "stdout": stdout.strip()[:50000] or "(no output)",
            "stderr": stderr.strip()[:10000],
            "exit_code": r.returncode,
        }
    except sp.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Error: Command timed out after {timeout}s", "exit_code": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "Error: Command not found. Check that the program is installed.", "exit_code": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": f"Error: {e}", "exit_code": -1}


# ============================================================
# Legacy routes (compatibility)
# ============================================================

@app.get("/api/run/{thread_id}/state")
async def get_legacy_run_state(thread_id: str):
    with runs_lock:
        run_info = active_runs.get(thread_id)

    if not run_info:
        return AgentStateResponse(
            messages=[],
            extra={"status": "not_found", "thread_id": thread_id},
        )

    return AgentStateResponse(
        messages=[],
        extra={
            "status": run_info.get("status", "unknown"),
            "thread_id": thread_id,
        },
    )


@app.get("/api/files")
async def list_files():
    files = []

    try:
        for root, dirs, filenames in os.walk(config_module.WORKSPACE_DIR):
            dirs[:] = [d for d in dirs if d not in (".backups", ".snapshots")]

            for filename in filenames:
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, config_module.WORKSPACE_DIR)

                try:
                    stat = os.stat(filepath)
                    files.append({
                        "path": relpath,
                        "is_dir": False,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    pass

            for dirname in dirs:
                dirpath = os.path.join(root, dirname)
                relpath = os.path.relpath(dirpath, config_module.WORKSPACE_DIR)
                files.append({
                    "path": relpath,
                    "is_dir": True,
                    "size": 0,
                })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取工作区失败: {e!s}")

    files.sort(key=lambda f: f["path"])

    return FileListResponse(files=[
        FileEntry(path=f["path"], is_dir=f["is_dir"], size=f["size"], mtime=f.get("mtime"))
        for f in files
    ])


@app.get("/api/files/{file_path:path}")
async def read_file(file_path: str):
    full_path = os.path.join(config_module.WORKSPACE_DIR, file_path)

    real_path = os.path.realpath(full_path)
    real_root = os.path.realpath(config_module.WORKSPACE_DIR)
    if os.path.commonpath([real_root, real_path]) != real_root:
        raise HTTPException(status_code=403, detail="禁止访问该路径")

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    if os.path.isdir(full_path):
        raise HTTPException(status_code=400, detail="这是一个目录，不是文件")

    try:
        stat = os.stat(full_path)

        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            content = "[二进制文件，无法显示内容]"

        ext = os.path.splitext(file_path)[1].lower()
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".html": "html",
            ".css": "css",
            ".json": "json",
            ".md": "markdown",
            ".txt": "text",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".sh": "bash",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".rs": "rust",
        }
        lang = lang_map.get(ext, "text")

        return FileContentResponse(
            content=content,
            size=stat.st_size,
            lines=content.count("\n") + 1,
            mtime=stat.st_mtime,
            lang=lang,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e!s}")


@app.get("/api/metrics")
async def get_metrics():
    summary = metrics_collector.dump_summary()
    llm_data = summary.get("llm", {})
    tool_data = summary.get("tool_calls", {})
    repair_data = summary.get("repair_cycles", {})

    current = MetricsCurrentResponse(
        total_llm_calls=llm_data.get("total_calls", 0),
        total_tokens=llm_data.get("total_tokens", 0),
        llm_latency_avg=llm_data.get("avg_latency_ms", 0.0),
        tool_calls=tool_data.get("total", 0),
        tool_successes=tool_data.get("successes", 0),
        tool_failures=tool_data.get("failures", 0),
        tool_success_rate=tool_data.get("success_rate", 0.0),
        repair_cycles=repair_data.get("total", 0),
        repair_cycles_recovered=sum(1 for o in repair_data.get("outcomes", []) if o.get("outcome") == "fixed"),
        last_updated=None,
        llm=MetricsLLMData(
            total_calls=llm_data.get("total_calls", 0),
            total_tokens=llm_data.get("total_tokens", 0),
            avg_tokens_per_call=llm_data.get("avg_tokens_per_call", 0.0),
            avg_latency_ms=llm_data.get("avg_latency_ms", 0.0),
            max_latency_ms=llm_data.get("max_latency_ms", 0.0),
            min_latency_ms=llm_data.get("min_latency_ms", 0.0),
        ),
        tool_calls_detail=MetricsToolData(
            total=tool_data.get("total", 0),
            successes=tool_data.get("successes", 0),
            failures=tool_data.get("failures", 0),
            success_rate=tool_data.get("success_rate", 0.0),
            failure_reasons=tool_data.get("failure_reasons", []),
        ),
        repair_cycles_detail=MetricsRepairData(
            total=repair_data.get("total", 0),
            outcomes=repair_data.get("outcomes", []),
        ),
    )

    historical = []
    if os.path.exists(METRICS_HISTORY_FILE):
        try:
            with open(METRICS_HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                historical = data
        except Exception:
            pass

    return MetricsResponse(current=current, historical=historical)


async def check_ollama_connected(base_url: str, timeout: float = 2.0) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


@app.get("/api/config")
async def get_config():
    llm_providers = {
        "openai": LLMProviderStatus(
            has_key=bool(os.getenv("OPENAI_API_KEY")),
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            base_url=os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL"),
        ),
        "anthropic": LLMProviderStatus(
            has_key=bool(os.getenv("ANTHROPIC_API_KEY")),
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        ),
        "ollama": LLMProviderStatus(
            has_key=True,
            model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            is_connected=await check_ollama_connected(
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            ),
        ),
        "deepseek": LLMProviderStatus(
            has_key=bool(os.getenv("DEEPSEEK_API_KEY")),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        ),
        "minimax": LLMProviderStatus(
            has_key=bool(os.getenv("MINIMAX_API_KEY")),
            model=os.getenv("MINIMAX_MODEL", "MiniMax-M2.7"),
            base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic"),
        ),
    }

    system_config = SystemConfig(
        workspace_dir=str(config_module.WORKSPACE_DIR),
        sandbox_image=os.getenv("SANDBOX_IMAGE", "python:3.10-slim"),
        sandbox_mem_limit=os.getenv("SANDBOX_MEM_LIMIT", "256m"),
        sandbox_timeout=int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60")),
        max_coder_steps=int(os.getenv("MAX_CODER_STEPS", "15")),
        max_planner_steps=int(os.getenv("MAX_PLANNER_STEPS", "10")),
        context_max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "8000")),
    )

    env_vars = []
    sensitive_keys = {"key", "secret", "token", "password"}

    for key, value in sorted(os.environ.items()):
        is_sensitive = any(s in key.lower() for s in sensitive_keys)
        env_vars.append(EnvVar(
            name=key,
            value="****" if is_sensitive and value else value,
            is_sensitive=is_sensitive,
            is_set=True,
        ))

    return ConfigResponse(
        llm_providers=llm_providers,
        system=system_config,
        env_vars=env_vars,
    )


@app.get("/api/snapshots")
async def list_snapshots():
    snapshots_dir = os.path.join(config_module.WORKSPACE_DIR, ".snapshots")
    snapshots = []

    if not os.path.exists(snapshots_dir):
        return SnapshotListResponse(snapshots=[])

    try:
        for entry in sorted(os.listdir(snapshots_dir), reverse=True):
            snapshot_path = os.path.join(snapshots_dir, entry)

            if not os.path.isdir(snapshot_path):
                continue

            metadata_path = os.path.join(snapshot_path, "metadata.json")
            metadata = {}

            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, encoding="utf-8") as f:
                        metadata = json.load(f)
                except Exception:
                    pass

            snapshots.append(SnapshotEntry(
                id=entry,
                timestamp=metadata.get("timestamp", ""),
                reason=metadata.get("reason", ""),
                active_files=metadata.get("active_files", []),
                active_files_count=len(metadata.get("active_files", [])),
            ))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取快照失败: {e!s}")

    return SnapshotListResponse(snapshots=snapshots)


@app.get("/api/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    snapshots_dir = os.path.join(config_module.WORKSPACE_DIR, ".snapshots")
    snapshot_path = os.path.join(snapshots_dir, snapshot_id)

    if not os.path.exists(snapshot_path):
        raise HTTPException(status_code=404, detail="快照不存在")

    result = SnapshotDetailResponse(metadata=SnapshotMetadata(timestamp="", reason="", active_files=[]), conversation_summary="", code_files=[])

    metadata_path = os.path.join(snapshot_path, "metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as f:
                metadata = json.load(f)
                result.metadata = SnapshotMetadata(
                    timestamp=metadata.get("timestamp", ""),
                    reason=metadata.get("reason", ""),
                    active_files=metadata.get("active_files", []),
                )
        except Exception:
            pass

    summary_path = os.path.join(snapshot_path, "conversation_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                result.conversation_summary = json.load(f)
        except Exception:
            pass

    code_dir = os.path.join(snapshot_path, "code")
    if os.path.exists(code_dir):
        for root, dirs, files in os.walk(code_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, code_dir)

                try:
                    with open(filepath, encoding="utf-8") as f:
                        content = f.read()
                    result.code_files.append(CodeFile(path=relpath, content=content))
                except Exception:
                    pass

    return result


@app.get("/api/backups")
async def list_backups():
    backups_dir = os.path.join(config_module.WORKSPACE_DIR, ".backups")
    backups = []

    if not os.path.exists(backups_dir):
        return BackupListResponse(backups=[])

    try:
        for entry in os.listdir(backups_dir):
            filepath = os.path.join(backups_dir, entry)

            if not os.path.isfile(filepath):
                continue

            stat = os.stat(filepath)
            backups.append(BackupEntry(
                name=entry,
                size=stat.st_size,
                mtime=stat.st_mtime,
            ))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {e!s}")

    backups.sort(key=lambda b: b.mtime, reverse=True)

    return BackupListResponse(backups=backups)


@app.get("/api/backups/{backup_name}")
async def read_backup(backup_name: str):
    backups_dir = os.path.join(config_module.WORKSPACE_DIR, ".backups")
    filepath = os.path.join(backups_dir, backup_name)

    real_path = os.path.realpath(filepath)
    real_root = os.path.realpath(backups_dir)
    if not real_path.startswith(real_root):
        raise HTTPException(status_code=403, detail="禁止访问")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="备份文件不存在")

    try:
        stat = os.stat(filepath)

        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        return BackupContentResponse(
            content=content,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {e!s}")


# ============================================================
# Todo List API
# ============================================================

from src.infra import db as nano_db

@app.get("/api/todos")
async def list_todos():
    try:
        todos = nano_db.todo_get_all()
        return {"todos": todos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/todos")
async def create_todo(title: str, priority: int = 0, category: str | None = None):
    try:
        item = nano_db.todo_create(title=title, priority=priority, category=category)
        return {"todo": item}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/todos/{todo_id}/complete")
async def complete_todo(todo_id: str):
    result = nano_db.todo_complete(todo_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"todo": result}

@app.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: str):
    deleted = nano_db.todo_delete(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"deleted": True}


# ============================================================
# Memory API
# ============================================================

from src.memory.manager import get_memory_manager

@app.get("/api/memories")
async def list_memories(category: str | None = None, min_importance: int = 0, limit: int = 50):
    try:
        mm = get_memory_manager()
        memories = mm.get(category=category, min_importance=min_importance, limit=limit)
        return {"memories": memories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/memories")
async def create_memory(content: str, category: str, importance: int = 1, tags: str = ""):
    try:
        import json
        tag_list = json.loads(tags) if tags else []
        mm = get_memory_manager()
        entry = mm.save(category=category, content=content, importance=importance, tags=tag_list)
        return {"memory": entry}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/memories/search")
async def search_memories(q: str, limit: int = 20):
    try:
        mm = get_memory_manager()
        results = mm.search(query=q, limit=limit)
        return {"memories": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/memories/{memory_id}")
async def update_memory(memory_id: str, content: str | None = None, importance: int | None = None):
    result = nano_db.memory_update(memory_id, content, importance)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": result}

@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str):
    deleted = nano_db.memory_delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# ============================================================
# Sub-Agent API
# ============================================================

@app.get("/api/subagents")
async def list_subagents():
    try:
        active = nano_db.subagent_get_active()
        return {"active": active}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/subagents/{subagent_id}")
async def get_subagent(subagent_id: str):
    result = nano_db.subagent_get(subagent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="SubAgent not found")
    return {"subagent": result}


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
    yield
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
