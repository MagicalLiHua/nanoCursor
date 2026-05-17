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

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from src.infra.messages import HumanMessage

# 导入 Pydantic API 模型
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
    ConversationCreateRequest,
    ConversationRunRequest,
    ConversationTeamRecommendRequest,
    ConversationTeamUpdateRequest,
    EnvVar,
    FileContentResponse,
    FileEntry,
    FileListResponse,
    ApprovalDecisionRequest,
    LLMProviderStatus,
    Message,
    MetricsCurrentResponse,
    MetricsLLMData,
    MemoryProfileResponse,
    MetricsRepairData,
    MetricsResponse,
    MetricsToolData,
    DeliveryScoreResponse,
    QualityGateResponse,
    PreferenceCreateRequest,
    RecoveryCenterResponse,
    RequirementTraceabilityResponse,
    RollbackRequest,
    RollbackResponse,
    RunBlueprintRequest,
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
)

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """统一错误响应格式"""
    code: str           # 例如 "VALIDATION_ERROR", "LLM_TIMEOUT"
    message: str        # 人类可读的错误描述
    details: dict | None = None
    request_id: str     # 本次请求的追踪 ID


class BashRequest(BaseModel):
    """Bash 命令执行请求"""
    command: str
    workspace_dir: str | None = None
    timeout: int = 120


def _http_status_to_code(status: int) -> str:
    """将 HTTP 状态码映射为业务错误码"""
    mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
        504: "GATEWAY_TIMEOUT",
    }
    return mapping.get(status, "UNKNOWN_ERROR")


# ============================================================
# 导入项目模块
# ============================================================

# 确保项目根目录在 sys.path 中
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 加载环境变量
load_dotenv(os.path.join(ROOT, ".env"))

# 导入工作流引擎（统一 engine.py）
from src.agent.engine import agent_loop, run_subagent, TOOLS, get_workdir, MODEL
import src.infra.config as config_module
from src.infra.metrics import metrics as metrics_collector

# 始终从 config_module 读取，保证获取最新值
def _get_workspace() -> str:
    return config_module.WORKSPACE_DIR


def _set_active_workspace(dir_path: str) -> str:
    """Switch the active workspace and reset workspace-scoped caches."""
    abs_path = os.path.abspath(dir_path)
    os.makedirs(abs_path, exist_ok=True)
    config_module.WORKSPACE_DIR = abs_path

    try:
        import src.tools.file_tools as file_tools_module

        file_tools_module.WORKSPACE_DIR = abs_path
        file_tools_module.BACKUP_DIR = os.path.join(abs_path, ".backups")
        os.makedirs(file_tools_module.BACKUP_DIR, exist_ok=True)
    except Exception:
        pass

    try:
        from src.indexer.indexer import reset_index

        reset_index()
    except Exception:
        pass

    try:
        from src.tools.git_tools import set_git_workspace

        set_git_workspace(Path(abs_path))
    except Exception:
        pass

    try:
        from src.agent.engine import reset_runtime_caches

        reset_runtime_caches()
    except Exception:
        pass

    return abs_path


def _workspace_for_thread(thread_id: str) -> str:
    with runs_lock:
        run_info = active_runs.get(thread_id)
        workspace_dir = run_info.get("workspace_dir") if run_info else None
    return workspace_dir or _get_workspace()

from src.agent.state import WorkflowCancelledError  # 保留导入，兼容旧接口
from src.api.services.agenthub_state import add_team_member, list_task_items, list_team_members
from src.api.services.artifact_service import build_artifact_center
from src.api.services.benchmark_service import emit_benchmark_run, get_benchmark, list_benchmarks
from src.api.services.blueprint_service import build_run_blueprint
from src.api.services.capability_service import build_capability_hub, import_workspace_skill, recommend_capabilities
from src.api.services.conversation_service import (
    create_conversation,
    finalize_conversation_run,
    get_conversation,
    link_run_to_conversation,
    list_conversations,
    refresh_conversation_recommendation,
    update_conversation_team,
)
from src.api.services.demo_run import DEMO_PROMPT, emit_demo_run
from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.api.services.sse_broker import get_sse_broker, stream_events_push, patch_event_store_for_push
# Enable push-based SSE: all events are automatically broadcast to connected clients
patch_event_store_for_push()
from src.api.services.orchestration_service import build_execution_plan, build_runtime_instructions
from src.api.services.quality_service import build_quality_gate
from src.api.services.preference_service import add_preference_memory, build_memory_profile
from src.api.services.recovery_service import build_recovery_center, rollback_from_backup
from src.api.services.report_service import build_delivery_report
from src.api.services.run_history import list_run_history
from src.api.services.run_context import RunContext
from src.api.services.score_service import build_delivery_score
from src.api.services.traceability_service import build_requirement_traceability
from src.api.services.tool_events import capability_trace_for_tool, derive_agenthub_events
from src.api.services.workspace_service import build_workspace_overview

# 持久化指标历史文件（项目根目录，跨工作区保留）
METRICS_HISTORY_FILE = os.path.join(ROOT, "metrics_history.json")

# ============================================================
# 创建 FastAPI 应用
# ============================================================

app = FastAPI(
    title="nanoCursor API",
    description="nanoCursor 智能体框架的后端 API 服务",
    version="2.0.0",
)

# Initialize SQLite database (create tables if not exist)
from src.infra.db import init_db
init_db()

# 配置 CORS，允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地开发，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """将所有 HTTPException 统一格式化为 ErrorResponse"""
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code=_http_status_to_code(exc.status_code),
            message=str(exc.detail),
            request_id=request_id,
        ).model_dump()
    )


# ============================================================
# 请求追踪中间件
# ============================================================

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """为每个请求注入 X-Request-ID，便于日志追踪"""
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# ============================================================
# 健康检查端点
# ============================================================

@app.get("/health")
async def health():
    """Liveness probe - Kubernetes 判断容器是否存活"""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness probe - 检查 LLM 提供商是否可用"""
    try:
        from src.agent.engine import create_client, MODEL
        client = create_client()
        # Quick API key check - try a minimal call
        return {"status": "ready", "llm": "available", "model": MODEL}
    except Exception as e:
        return {"status": "degraded", "llm": "unavailable", "error": str(e)}, 503


@app.get("/version")
async def version():
    """返回当前服务版本"""
    import subprocess
    version_str = "2.1.0"
    commit_sha = os.getenv("COMMIT_SHA", "")
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            commit_sha = result.stdout.strip()
    except Exception:
        pass
    return {"version": version_str, "commit": commit_sha or "dev"}


# ============================================================
# 活跃运行管理
# ============================================================

# 存储每个线程的运行上下文
active_runs: dict[str, RunContext] = {}
# 线程锁，保护 active_runs 的并发访问
runs_lock = threading.Lock()
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
    return run_info


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
# API 限流管理
# ============================================================

import time as _time

# 每个线程最近一次启动工作流的时间（用于频率限制）
_workflow_start_times: dict[str, list[float]] = {}

# 同一线程最小启动间隔（秒），防止频繁启动
_WORKFLOW_MIN_INTERVAL_SECONDS = 10


def _check_rate_limit(thread_id: str) -> tuple[bool, str]:
    """
    检查是否可以启动新工作流。

    返回 (允许, 错误消息)。若返回 (False, msg)，调用方应拒绝启动。
    """
    now = _time.time()

    # 1. 检查该线程是否已有运行中的工作流
    with runs_lock:
        run_info = active_runs.get(thread_id)
    if run_info and run_info.get("status") == "running":
        return False, f"线程 {thread_id} 已有一个工作流在运行中，请等待完成后再试"

    # 2. 频率限制：同线程两次启动间隔不得少于 WORKFLOW_MIN_INTERVAL_SECONDS
    last_times = _workflow_start_times.get(thread_id, [])
    recent = [t for t in last_times if now - t < _WORKFLOW_MIN_INTERVAL_SECONDS]
    if recent:
        wait_time = int(_WORKFLOW_MIN_INTERVAL_SECONDS - (now - max(recent)))
        return False, f"工作流启动过于频繁，请等待 {wait_time} 秒后再试"

    # 记录本次启动时间
    _workflow_start_times.setdefault(thread_id, []).append(now)
    # 只保留最近 10 条记录
    if len(_workflow_start_times[thread_id]) > 10:
        _workflow_start_times[thread_id] = _workflow_start_times[thread_id][-10:]

    return True, ""


def _run_workflow(thread_id: str, initial_messages: list, workspace_dir: str, max_retries: int = 3, max_coder_steps: int = 15):
    """
    在后台线程中运行 agent_loop 工作流。

    参数:
        thread_id: 会话的唯一标识符
        initial_messages: 用户输入的对话消息列表
        max_retries: 保留参数（兼容旧接口）
        max_coder_steps: 保留参数（兼容旧接口）
    """
    # 创建新的 event loop 在这个线程中
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_workflow_async(thread_id, initial_messages, max_retries, max_coder_steps, workspace_dir)
        )
    finally:
        loop.close()


async def _run_workflow_async(thread_id: str, initial_messages: list, max_retries: int, max_coder_steps: int, workspace_dir: str | None = None):
    """_run_workflow 的异步内部实现。"""

    with runs_lock:
        run_info = active_runs.get(thread_id)
        if not run_info:
            return
        q = run_info["queue"]
        workspace_dir = workspace_dir or run_info.get("workspace_dir")
        execution_plan = run_info.get("execution_plan", {})
        run_team = run_info.get("team", [])
    workspace_dir = workspace_dir or _get_workspace()

    # 构建消息格式（base_engine 使用 {"role": ..., "content": ...}）
    messages = [{"role": m.type if hasattr(m, 'type') else 'user', "content": m.content} for m in initial_messages]

    # 构建系统提示
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

    def on_tool_call(tool_name: str, tool_input: dict, output: str):
        """每次工具调用后发送事件到 SSE（含实时指标）"""
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
            on_tool_call=on_tool_call,
        )
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
        _finalize_conversation_for_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="failed",
            error=str(e),
        )
    finally:
        # 持久化指标——每次任务结束都写入文件
        try:
            metrics_collector.flush_to_file()
            metrics_collector.append_to_history(METRICS_HISTORY_FILE, tag=thread_id[:8])
        except Exception:
            pass
        with runs_lock:
            if thread_id in active_runs:
                active_runs[thread_id].set_status(final_status)


def _extract_node_event(node_name: str, node_state: dict) -> dict:
    """
    从节点状态中提取关键信息，简化后返回给前端。

    不同的节点返回不同的数据字段，这个函数负责统一格式。

    参数:
        node_name: 节点名称 (supervisor, planner, coder, sandbox, reviewer, verifier 等)
        node_state: 节点返回的状态字典

    返回:
        简化后的事件数据字典
    """
    data = {}

    # ---- Supervisor: 路由决策 + 任务池状态 ----
    if node_name == "supervisor":
        data["last_action"] = node_state.get("last_action", "")
        data["current_task_id"] = node_state.get("current_task_id")
        data["step_budget"] = node_state.get("step_budget", 0)
        if node_state.get("task_pool"):
            data["task_pool"] = node_state["task_pool"]

    # ---- Planner: 当前计划文本 ----
    elif node_name == "planner":
        data["current_plan"] = node_state.get("current_plan", "")

    # ---- Coder: 最新消息内容 ----
    elif node_name == "coder":
        _extract_messages_content(node_state, data)

    # ---- Sandbox: 错误跟踪 + 重试信息 ----
    elif node_name == "sandbox":
        data["error_trace"] = node_state.get("error_trace", "")
        data["retry_count"] = node_state.get("retry_count", 0)
        data["max_retries"] = node_state.get("max_retries", 3)

    # ---- Reviewer: 诊断内容 ----
    elif node_name == "reviewer":
        _extract_messages_content(node_state, data)

    # ---- Verifier: 验证结果 ----
    elif node_name == "verifier":
        data["verification_passed"] = node_state.get("verification_passed")
        data["verification_result"] = node_state.get("verification_result")
        _extract_messages_content(node_state, data)

    # 每个 node_update 附带上实时指标，前端据此更新侧边栏
    data["metrics"] = metrics_collector.dump_summary()
    return data


def _extract_messages_content(node_state: dict, data: dict) -> None:
    """从 node_state 中提取最新消息的文本内容，写入 data['content']。"""
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
# API 路由
# ============================================================

@app.post("/api/run")
async def start_run(request: RunRequest):
    """
    启动一个新的工作流运行。

    接收用户提示，创建新的线程 ID，在后台启动 LangGraph 工作流。

    请求体:
        {
            "prompt": "用户输入的需求描述",
            "thread_id": "可选的已有线程 ID，用于继续对话",
            "messages": "可选的对话历史消息列表"
        }

    返回:
        {
            "thread_id": "会话线程 ID",
            "status": "started"
        }
    """
    prompt = request.prompt
    # 使用已有的 thread_id 或创建新的
    thread_id = request.thread_id or str(uuid.uuid4())

    # 如果请求中包含工作目录，则更新 config_module.WORKSPACE_DIR
    if request.workspace_dir:
        abs_path = _set_active_workspace(request.workspace_dir)
        print(f"[API] 设置工作区: {abs_path}")

    # 限流检查：防止频繁启动或并发启动
    allowed, rate_limit_msg = _check_rate_limit(thread_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=rate_limit_msg)

    # 构建初始消息（每个分支保证赋值）
    # 优先级：request.messages > 单条prompt
    if request.messages:
        # 前端传入的新对话，直接构建 messages 列表
        initial_messages = [HumanMessage(content=m.content) for m in request.messages]
        initial_messages.append(HumanMessage(content=prompt))
        print(f"[API] 使用前端传入历史消息 {len(request.messages)} 条，新 prompt 已追加")
    else:
        initial_messages = [HumanMessage(content=prompt)]
        print(f"[API] 开始新会话")

    print(f"[API] 构建 initial_messages 完成，共 {len(initial_messages)} 条消息")

    # 创建事件队列
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
        active_runs[thread_id] = run_context

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

    # 在后台线程中启动工作流
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
    """
    SSE (Server-Sent Events) 端点，流式返回工作流事件。

    前端通过 EventSource 连接此端点，实时接收节点执行状态。

    事件格式:
        event: node_update
        data: {"type": "node_update", "node": "planner", "data": {...}}

        event: done
        data: {"type": "done", "status": "completed"}

        event: error
        data: {"type": "error", "message": "错误信息"}

    参数:
        thread_id: 会话线程 ID

    返回:
        text/event-stream 格式的 SSE 事件流
    """
    # 获取运行信息
    run_info = active_runs.get(thread_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    q = run_info["queue"]

    def event_generator():
        """生成 SSE 事件流的生成器函数。"""
        while True:
            try:
                # 从队列中获取事件，设置超时避免永久阻塞
                item = q.get(timeout=300)  # 5 分钟超时

                if item is None:
                    # None 表示流结束
                    break

                # 按照 SSE 格式发送事件
                event_type = json.loads(item).get("type", "message")
                yield f"event: {event_type}\ndata: {item}\n\n"

                # 如果是 done 或 error 事件，结束流
                if event_type in ("done", "error"):
                    break

            except queue.Empty:
                # 超时，发送心跳保持连接
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
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


@app.post("/api/runs")
async def start_agenthub_run(request: RunRequest):
    """启动 AgentHub 标准运行接口（保留旧 /api/run 作为兼容入口）。"""
    return await start_run(request)


@app.post("/api/conversations")
async def create_agenthub_conversation(request: ConversationCreateRequest):
    """创建独立会话上下文，并按首条需求生成推荐团队。"""
    return {
        "conversation": create_conversation(
            prompt=request.prompt,
            workspace_dir=request.workspace_dir or _get_workspace(),
        )
    }


@app.get("/api/conversations")
async def list_agenthub_conversations(limit: int = 50, workspace_dir: str | None = None):
    """列出当前工作区的 AgentHub 会话。"""
    safe_limit = min(max(limit, 0), 200)
    return {
        "conversations": list_conversations(
            workspace_dir=workspace_dir or _get_workspace(),
            limit=safe_limit,
        )
    }


@app.get("/api/conversations/{conversation_id}")
async def get_agenthub_conversation(conversation_id: str, workspace_dir: str | None = None):
    """获取会话上下文、当前团队和运行绑定。"""
    conversation = get_conversation(conversation_id, workspace_dir or _get_workspace())
    if not conversation:
        raise HTTPException(status_code=404, detail="未找到该会话")
    return {"conversation": conversation}


@app.post("/api/conversations/{conversation_id}/team/recommend")
async def recommend_agenthub_conversation_team(
    conversation_id: str,
    request: ConversationTeamRecommendRequest,
):
    """重新生成并保存本会话的智能组队建议。"""
    try:
        result = refresh_conversation_recommendation(
            conversation_id=conversation_id,
            prompt=request.prompt,
            workspace_dir=request.workspace_dir or _get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@app.put("/api/conversations/{conversation_id}/team")
async def update_agenthub_conversation_team(
    conversation_id: str,
    request: ConversationTeamUpdateRequest,
):
    """保存用户对本会话 Agent 群组的增删改。"""
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
async def start_agenthub_conversation_run(
    conversation_id: str,
    request: ConversationRunRequest,
):
    """在会话上下文中启动一次隔离 Agent 运行。"""
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
async def start_agenthub_demo_run(request: RunRequest):
    """启动不依赖 LLM 的稳定 AgentHub 演示运行。"""
    prompt = request.prompt or DEMO_PROMPT
    thread_id = request.thread_id or f"demo-{uuid.uuid4()}"

    if request.workspace_dir:
        _set_active_workspace(request.workspace_dir)

    q = queue.Queue()
    approval_event = threading.Event()
    run_workspace = _get_workspace()
    with runs_lock:
        active_runs[thread_id] = RunContext(
            thread_id=thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            mode="agenthub_demo",
            approval_event=approval_event,
        )

    event_store.create_session(
        thread_id=thread_id,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
        mode="agenthub_demo",
    )
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Demo Run 已启动",
        content=prompt,
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": "agenthub_demo"},
        workspace_dir=run_workspace,
    )

    def update_status(status: str):
        with runs_lock:
            if thread_id in active_runs:
                active_runs[thread_id].set_status(status)

    def wait_for_approval(timeout_seconds: float) -> str | None:
        approval_event.wait(timeout_seconds)
        with runs_lock:
            run_info = active_runs.get(thread_id) or {}
            return run_info.get("approval_decision")

    t = threading.Thread(
        target=emit_demo_run,
        kwargs={
            "thread_id": thread_id,
            "workspace_dir": run_workspace,
            "store": event_store,
            "status_callback": update_status,
            "approval_waiter": wait_for_approval,
        },
        daemon=True,
    )
    active_runs[thread_id].thread = t
    t.start()

    return RunResponse(thread_id=thread_id, status="started")


@app.get("/api/benchmarks")
async def get_agenthub_benchmarks():
    """获取固定基准任务目录。"""
    return BenchmarkListResponse(benchmarks=list_benchmarks(_get_workspace()))


@app.post("/api/benchmarks/run")
async def start_agenthub_benchmark_run(request: BenchmarkRunRequest):
    """启动一个固定 Benchmark 运行。"""
    try:
        benchmark = get_benchmark(request.benchmark_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    thread_id = request.thread_id or f"benchmark-{request.benchmark_id}-{uuid.uuid4()}"

    if request.workspace_dir:
        _set_active_workspace(request.workspace_dir)

    run_workspace = _get_workspace()
    q = queue.Queue()
    with runs_lock:
        active_runs[thread_id] = RunContext(
            thread_id=thread_id,
            workspace_dir=run_workspace,
            queue=q,
            status="running",
            mode="agenthub_benchmark",
            metadata={"benchmark_id": request.benchmark_id},
        )

    event_store.create_session(
        thread_id=thread_id,
        prompt=benchmark["prompt"],
        workspace_dir=run_workspace,
        status="running",
        mode="agenthub_benchmark",
    )
    event_store.update_session(thread_id, run_workspace, benchmark_id=request.benchmark_id)
    _emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Benchmark Run 已启动",
        content=benchmark["prompt"],
        payload={
            "workspace_dir": run_workspace,
            "thread_id": thread_id,
            "benchmark_id": request.benchmark_id,
            "mode": "agenthub_benchmark",
        },
        workspace_dir=run_workspace,
    )

    def update_status(status: str):
        with runs_lock:
            if thread_id in active_runs:
                active_runs[thread_id].set_status(status)

    t = threading.Thread(
        target=emit_benchmark_run,
        kwargs={
            "thread_id": thread_id,
            "benchmark_id": request.benchmark_id,
            "workspace_dir": run_workspace,
            "store": event_store,
            "status_callback": update_status,
        },
        daemon=True,
    )
    active_runs[thread_id].thread = t
    t.start()

    return BenchmarkRunResponse(
        thread_id=thread_id,
        status="started",
        benchmark_id=request.benchmark_id,
        title=benchmark["title"],
    )


@app.get("/api/runs")
async def list_agenthub_runs(
    status: str | None = None,
    mode: str | None = None,
    limit: int = 50,
):
    """列出 AgentHub 历史运行摘要。"""
    safe_limit = min(max(limit, 0), 200)
    return RunHistoryResponse(
        runs=list_run_history(
            workspace_dir=_get_workspace(),
            status=status,
            mode=mode,
            limit=safe_limit,
        )
    )


@app.get("/api/runs/{thread_id}")
async def get_agenthub_run(thread_id: str):
    """获取 AgentHub 运行会话状态。"""
    with runs_lock:
        run_info = active_runs.get(thread_id)
        run_workspace = run_info.get("workspace_dir") if run_info else None
    run_workspace = run_workspace or _get_workspace()
    session = event_store.get_session(thread_id, run_workspace)

    if not session and not run_info:
        raise HTTPException(status_code=404, detail="未找到该运行记录")

    if session is None:
        session = {
            "thread_id": thread_id,
            "workspace_dir": run_workspace,
            "status": run_info.get("status", "unknown") if run_info else "unknown",
            "prompt": "",
            "mode": "agenthub_delivery",
            "created_at": None,
            "updated_at": None,
        }
    elif run_info and run_info.get("status"):
        session["status"] = run_info["status"]

    return RunSessionResponse(**session)


@app.get("/api/runs/{thread_id}/events/history")
async def get_agenthub_event_history(thread_id: str):
    """获取 AgentHub 运行历史事件，供刷新页面后恢复状态。"""
    workspace_dir = _workspace_for_thread(thread_id)
    session = event_store.get_session(thread_id, workspace_dir)
    if not session:
        raise HTTPException(status_code=404, detail="未找到该运行记录")
    return RunEventsResponse(events=event_store.list_events(thread_id, workspace_dir))


@app.post("/api/runs/{thread_id}/approval")
async def resolve_agenthub_approval(thread_id: str, request: ApprovalDecisionRequest):
    """记录用户对 Agent 计划的审批结果。"""
    workspace_dir = _workspace_for_thread(thread_id)
    session = event_store.get_session(thread_id, workspace_dir)
    with runs_lock:
        run_info = active_runs.get(thread_id)

    if not session and not run_info:
        raise HTTPException(status_code=404, detail="未找到该运行记录")

    decision = request.decision.strip().lower()
    if decision not in {"approved", "revise", "rejected"}:
        raise HTTPException(status_code=400, detail="审批结果必须是 approved、revise 或 rejected")

    event = event_store.append_event(
        thread_id=thread_id,
        event_type="approval_resolved",
        title=_approval_title(decision),
        content=request.comment or _approval_title(decision),
        agent="user",
        payload={
            "plan_id": request.plan_id or "default-plan",
            "decision": decision,
            "comment": request.comment,
        },
        workspace_dir=workspace_dir,
    )

    with runs_lock:
        current_run = active_runs.get(thread_id)
        if current_run:
            current_run.resolve_approval(decision)

    return event


@app.get("/api/runs/{thread_id}/diff")
async def get_agenthub_run_diff(thread_id: str):
    """获取本次运行的文件变更和 unified diff。"""
    return get_run_diff(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/report")
async def get_agenthub_run_report(thread_id: str):
    """获取或生成本次运行的交付报告。"""
    return build_delivery_report(thread_id, _workspace_for_thread(thread_id))


@app.get("/api/runs/{thread_id}/quality")
async def get_agenthub_run_quality(thread_id: str):
    """获取本次运行的交付质量门禁结果。"""
    return QualityGateResponse(**build_quality_gate(thread_id, _workspace_for_thread(thread_id)))


@app.get("/api/runs/{thread_id}/score")
async def get_agenthub_run_score(thread_id: str):
    """获取本次运行的交付评分。"""
    return DeliveryScoreResponse(**build_delivery_score(thread_id, _workspace_for_thread(thread_id)))


@app.get("/api/runs/{thread_id}/traceability")
async def get_agenthub_run_traceability(thread_id: str):
    """获取本次运行的需求追踪矩阵。"""
    return RequirementTraceabilityResponse(
        **build_requirement_traceability(thread_id, _workspace_for_thread(thread_id))
    )


@app.get("/api/runs/{thread_id}/artifacts")
async def get_agenthub_run_artifacts(thread_id: str):
    """获取本次运行的交付物中心。"""
    return ArtifactCenterResponse(**build_artifact_center(thread_id, _workspace_for_thread(thread_id)))


@app.get("/api/runs/{thread_id}/recovery")
async def get_agenthub_run_recovery(thread_id: str):
    """获取本次运行的安全与恢复状态。"""
    return RecoveryCenterResponse(**build_recovery_center(thread_id, _workspace_for_thread(thread_id)))


@app.get("/api/runs/{thread_id}/events")
async def stream_agenthub_events(thread_id: str):
    """
    AgentHub push-based SSE 事件流。

    事件通过 asyncio.Queue 实时推送，客户端可立即收到。
    先回放历史事件，然后订阅实时推送。
    支持多个前端同时订阅同一 thread。
    """
    with runs_lock:
        run_info = active_runs.get(thread_id)
        run_workspace = run_info.get("workspace_dir") if run_info else None
    run_workspace = run_workspace or _get_workspace()
    session = event_store.get_session(thread_id, run_workspace)

    if not session and not run_info:
        raise HTTPException(status_code=404, detail="未找到该运行记录")

    return StreamingResponse(
        stream_events_push(thread_id, run_workspace),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/tasks")
async def get_agenthub_tasks():
    """获取 AgentHub 标准化任务列表。"""
    return {"tasks": list_task_items(_get_workspace())}


@app.get("/api/team")
async def get_agenthub_team():
    """获取 AgentHub 标准化团队成员状态。"""
    return {"members": list_team_members(_get_workspace())}


@app.get("/api/capabilities")
async def get_agenthub_capabilities():
    """获取 AgentHub 能力中心：内置工具、MCP 连接器和 Skills。"""
    return build_capability_hub(_get_workspace())


@app.post("/api/capabilities/recommend")
async def recommend_agenthub_capabilities(request: CapabilityRecommendRequest):
    """根据用户需求推荐 Agent 组合和能力包。"""
    return recommend_capabilities(request.prompt, _get_workspace())


@app.post("/api/capabilities/skills")
async def import_agenthub_skill(request: SkillImportRequest):
    """导入工作区自定义 Skill。"""
    try:
        skill = import_workspace_skill(
            name=request.name,
            description=request.description,
            content=request.content,
            workspace_dir=_get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"skill": skill, "hub": build_capability_hub(_get_workspace())}


@app.post("/api/runs/blueprint")
async def create_agenthub_run_blueprint(request: RunBlueprintRequest):
    """根据用户需求生成运行前执行蓝图。"""
    return build_run_blueprint(request.prompt, _get_workspace())


@app.post("/api/team/agents")
async def create_agenthub_team_agent(request: TeamAgentCreateRequest):
    """创建自定义 Agent 角色卡。"""
    try:
        member = add_team_member(
            name=request.name,
            role=request.role,
            goal=request.goal,
            tools=request.tools,
            capabilities=request.capabilities,
            workspace_dir=_get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"member": member, "members": list_team_members(_get_workspace())}


@app.get("/api/preferences/profile")
async def get_agenthub_memory_profile(min_importance: int = 0):
    """获取用户偏好档案。"""
    return MemoryProfileResponse(
        **build_memory_profile(_get_workspace(), min_importance=min_importance)
    )


@app.post("/api/preferences")
async def create_agenthub_preference(request: PreferenceCreateRequest):
    """保存一条用户偏好记忆。"""
    try:
        memory = add_preference_memory(
            preference_type=request.preference_type,
            content=request.content,
            importance=request.importance,
            workspace_dir=_get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"memory": memory, "profile": build_memory_profile(_get_workspace())}


@app.get("/api/recovery")
async def get_agenthub_recovery():
    """获取当前工作区安全与恢复状态。"""
    return RecoveryCenterResponse(**build_recovery_center(None, _get_workspace()))


@app.post("/api/recovery/rollback")
async def rollback_agenthub_file(request: RollbackRequest):
    """从指定备份回滚文件。"""
    try:
        return RollbackResponse(
            **rollback_from_backup(
                backup_name=request.backup_name,
                target_path=request.target_path,
                workspace_dir=_get_workspace(),
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workspaces")
async def list_workspaces():
    """
    列出可用的工作区目录。
    返回项目根目录下的 workspace* 目录列表。

    返回:
        {"workspaces": ["workspace", "workspace2", ...]}
    """
    root = config_module.PROJECT_ROOT
    workspaces = []
    try:
        for entry in os.listdir(root):
            path = os.path.join(root, entry)
            if os.path.isdir(path) and entry.startswith("workspace"):
                workspaces.append(entry)
        workspaces.sort()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取工作区失败: {e!s}")

    return {
        "workspaces": workspaces,
        "current_workspace": _get_workspace(),
        "project_root": config_module.PROJECT_ROOT,
    }


@app.get("/api/workspace/overview")
async def get_workspace_overview(workspace_dir: str | None = None):
    """获取当前项目目录的会话、运行、能力、恢复点和代码索引摘要。"""
    return build_workspace_overview(workspace_dir or _get_workspace())


@app.post("/api/workspaces")
async def set_workspace(request: dict):
    """
    设置当前工作区目录。

    请求体:
        {"dir": "D:\\projects\\myapp"}

    返回:
        {"success": true, "workspace_dir": "..."}
    """
    dir_path = request.get("dir", "")
    if not dir_path:
        raise HTTPException(status_code=400, detail="工作目录路径不能为空")

    # 安全检查：确保路径是绝对路径
    if not os.path.isabs(dir_path):
        raise HTTPException(status_code=400, detail="请输入绝对路径")

    try:
        full_path = _set_active_workspace(dir_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法创建目录: {e!s}")

    return {"success": True, "workspace_dir": full_path}


@app.post("/api/run/{thread_id}/cancel")
async def cancel_run(thread_id: str):
    """
    取消指定线程的运行中的工作流。

    通过设置 active_runs 中状态为 "cancelled"，agent_loop 下次迭代时会检查并退出。
    """
    with runs_lock:
        run_info = active_runs.get(thread_id)

    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    if run_info.get("status") != "running":
        raise HTTPException(status_code=400, detail=f"工作流状态为 {run_info.get('status')}，无法取消")

    # 直接标记为取消状态
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


@app.post("/api/bash")
async def run_bash_command(request: BashRequest):
    """
    直接执行 bash 命令（不走 agent loop）。

    在 config_module.WORKSPACE_DIR 中运行命令并返回 stdout/stderr。
    包含危险命令过滤。

    请求体:
        {"command": "dir", "workspace_dir": "D:\\projects\\myapp", "timeout": 120}

    返回:
        {"success": true, "stdout": "...", "stderr": "...", "exit_code": 0}
    """
    import subprocess as sp

    command = request.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="命令不能为空")

    # 使用请求中指定的目录，否则使用全局工作区
    work_dir = request.workspace_dir or config_module.WORKSPACE_DIR
    work_dir = os.path.abspath(work_dir)

    # 安全检查：危险命令过滤
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
        # GBK decode for Windows, fallback to UTF-8
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


@app.get("/api/run/{thread_id}/state")
async def get_run_state(thread_id: str):
    """
    获取指定线程的当前状态（最终状态）。

    注意：base_engine 不使用 checkpointer，状态仅在运行期间可用。
    """
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
    """
    列出工作区中的所有文件和目录树。

    扫描工作区目录，返回文件树结构，排除 .backups 和 .snapshots 目录。

    返回:
        {
            "files": [
                {"path": "relative/path", "is_dir": true/false, "size": 1234},
                ...
            ]
        }
    """
    files = []

    try:
        for root, dirs, filenames in os.walk(config_module.WORKSPACE_DIR):
            # 排除备份和快照目录
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

            # 也添加目录节点
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

    # 按路径排序，方便前端展示
    files.sort(key=lambda f: f["path"])

    return FileListResponse(files=[
        FileEntry(path=f["path"], is_dir=f["is_dir"], size=f["size"], mtime=f.get("mtime"))
        for f in files
    ])


@app.get("/api/files/{file_path:path}")
async def read_file(file_path: str):
    """
    读取指定文件的内容。

    参数:
        file_path: 相对于 config_module.WORKSPACE_DIR 的文件路径

    返回:
        {
            "content": "文件内容",
            "size": 1234,
            "lines": 42,
            "mtime": 1234567890.0,
            "lang": "python"
        }
    """
    # 构建完整文件路径
    full_path = os.path.join(config_module.WORKSPACE_DIR, file_path)

    # 安全检查：防止路径遍历攻击
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

        # 尝试读取文件内容
        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            # 如果是二进制文件，返回提示
            content = "[二进制文件，无法显示内容]"

        # 根据扩展名推断语言
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
    """
    获取指标数据。

    从 MetricsCollector 单例获取当前运行指标，
    同时读取 workspace/metrics.json 获取历史数据。

    返回:
        {
            "current": { ... },  # 当前指标
            "historical": [...]   # 历史指标记录
        }
    """
    # 获取当前指标
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
        # 旧嵌套字段
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

    # 读取持久化的历史指标（跨工作区保留）
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
    """检测 Ollama 服务是否真正可连接。"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


@app.get("/api/config")
async def get_config():
    """
    获取配置信息。

    返回 LLM 提供商状态、系统配置和环境变量（敏感信息脱敏）。

    返回:
        {
            "llm_providers": { ... },
            "system": { ... },
            "env_vars": [...]
        }
    """
    # LLM 提供商状态
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
            has_key=True,  # Ollama 不需要 API key
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

    # 系统配置
    system_config = SystemConfig(
        workspace_dir=str(config_module.WORKSPACE_DIR),
        sandbox_image=os.getenv("SANDBOX_IMAGE", "python:3.10-slim"),
        sandbox_mem_limit=os.getenv("SANDBOX_MEM_LIMIT", "256m"),
        sandbox_timeout=int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60")),
        max_coder_steps=int(os.getenv("MAX_CODER_STEPS", "15")),
        max_planner_steps=int(os.getenv("MAX_PLANNER_STEPS", "10")),
        context_max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "8000")),
    )

    # 环境变量列表（敏感信息脱敏）
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
    """
    列出所有恢复快照。

    扫描 workspace/.snapshots/ 目录，返回每个快照的元数据。

    返回:
        {
            "snapshots": [
                {
                    "id": "snapshot_name",
                    "timestamp": "2024-01-01T12:00:00",
                    "reason": "max_retries_reached",
                    "active_files_count": 3,
                },
                ...
            ]
        }
    """
    snapshots_dir = os.path.join(config_module.WORKSPACE_DIR, ".snapshots")
    snapshots = []

    if not os.path.exists(snapshots_dir):
        return SnapshotListResponse(snapshots=[])

    try:
        for entry in sorted(os.listdir(snapshots_dir), reverse=True):
            snapshot_path = os.path.join(snapshots_dir, entry)

            if not os.path.isdir(snapshot_path):
                continue

            # 读取元数据
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
    """
    获取指定快照的详细信息。

    返回快照的元数据和包含的代码文件内容。

    参数:
        snapshot_id: 快照目录名称

    返回:
        {
            "metadata": { ... },
            "conversation_summary": "...",
            "code_files": [
                {"path": "...", "content": "..."},
                ...
            ]
        }
    """
    snapshots_dir = os.path.join(config_module.WORKSPACE_DIR, ".snapshots")
    snapshot_path = os.path.join(snapshots_dir, snapshot_id)

    if not os.path.exists(snapshot_path):
        raise HTTPException(status_code=404, detail="快照不存在")

    result = SnapshotDetailResponse(metadata=SnapshotMetadata(timestamp="", reason="", active_files=[]), conversation_summary="", code_files=[])

    # 读取元数据
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

    # 读取对话摘要
    summary_path = os.path.join(snapshot_path, "conversation_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                result.conversation_summary = json.load(f)
        except Exception:
            pass

    # 读取代码文件
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
    """
    列出所有文件备份。

    扫描 workspace/.backups/ 目录，返回每个备份文件的信息。

    返回:
        {
            "backups": [
                {"name": "filename.py.bak", "size": 1234, "mtime": ...},
                ...
            ]
        }
    """
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

    # 按修改时间倒序排列
    backups.sort(key=lambda b: b.mtime, reverse=True)

    return BackupListResponse(backups=backups)


@app.get("/api/backups/{backup_name}")
async def read_backup(backup_name: str):
    """
    读取指定备份文件的内容。

    参数:
        backup_name: 备份文件名

    返回:
        {
            "content": "文件内容",
            "size": 1234,
            "mtime": ...
        }
    """
    backups_dir = os.path.join(config_module.WORKSPACE_DIR, ".backups")
    filepath = os.path.join(backups_dir, backup_name)

    # 安全检查
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
    """List all todo items."""
    try:
        todos = nano_db.todo_get_all()
        return {"todos": todos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/todos")
async def create_todo(title: str, priority: int = 0, category: str | None = None):
    """Create a new todo item."""
    try:
        item = nano_db.todo_create(title=title, priority=priority, category=category)
        return {"todo": item}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/todos/{todo_id}/complete")
async def complete_todo(todo_id: str):
    """Mark a todo as completed."""
    result = nano_db.todo_complete(todo_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"todo": result}

@app.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: str):
    """Delete a todo item."""
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
    """List memories, optionally filtered."""
    try:
        mm = get_memory_manager()
        memories = mm.get(category=category, min_importance=min_importance, limit=limit)
        return {"memories": memories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/memories")
async def create_memory(content: str, category: str, importance: int = 1, tags: str = ""):
    """Store a new memory entry."""
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
    """Full-text search on memories."""
    try:
        mm = get_memory_manager()
        results = mm.search(query=q, limit=limit)
        return {"memories": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/memories/{memory_id}")
async def update_memory(memory_id: str, content: str | None = None, importance: int | None = None):
    """Update a memory entry."""
    result = nano_db.memory_update(memory_id, content, importance)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": result}

@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory entry."""
    deleted = nano_db.memory_delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# ============================================================
# Sub-Agent API
# ============================================================

@app.get("/api/subagents")
async def list_subagents():
    """List all subagents (active and recent)."""
    try:
        active = nano_db.subagent_get_active()
        return {"active": active}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/subagents/{subagent_id}")
async def get_subagent(subagent_id: str):
    """Get a specific subagent."""
    result = nano_db.subagent_get(subagent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="SubAgent not found")
    return {"subagent": result}


# ============================================================
# 静态文件服务（生产环境）
# ============================================================

def serve_frontend(production: bool = False):
    """
    配置前端静态文件服务。

    在生产模式下，服务 frontend/dist 目录中的构建产物。
    在开发模式下，不挂载静态文件，使用 Vite 开发服务器。

    参数:
        production: 是否为生产模式
    """
    if not production:
        return

    dist_dir = os.path.join(ROOT, "frontend", "dist")

    if os.path.exists(dist_dir):
        # 挂载静态文件
        app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_index(full_path: str):
            # 所有非 API 路由都返回 index.html（SPA 路由）
            index_path = os.path.join(dist_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend 未构建")


# ============================================================
# 启动入口
# ============================================================

# Persistence file for active runs (for graceful shutdown recovery)
ACTIVE_RUNS_STATE_FILE = os.path.join(config_module.PROJECT_ROOT, ".nanocursor", "active_runs_state.json")

def _save_active_runs_state():
    """Persist active runs to disk for recovery after restart."""
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
    """On startup, mark any previously active runs as interrupted."""
    if not os.path.exists(ACTIVE_RUNS_STATE_FILE):
        return
    try:
        with open(ACTIVE_RUNS_STATE_FILE, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    recovered = 0
    for tid, info in snapshot.items():
        ws_dir = info.get("workspace_dir", _get_workspace())
        session = event_store.get_session(tid, ws_dir)
        if session and session.get("status") == "running":
            event_store.update_session(tid, ws_dir, status="interrupted",
                error="Server was shut down while this run was active. You can restart it.")
            event_store.append_event(
                thread_id=tid, event_type="error",
                title="运行中断", content="服务在运行期间关闭。该运行已标记为 interrupted，可重新启动。",
                agent="system", payload={"reason": "server_shutdown"},
                workspace_dir=ws_dir,
            )
            recovered += 1

    if recovered:
        print(f"[startup] Recovered {recovered} interrupted run(s)")
    # Clean up state file
    try:
        os.remove(ACTIVE_RUNS_STATE_FILE)
    except OSError:
        pass

# Register shutdown and startup handlers via FastAPI lifespan
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: recover interrupted runs. Shutdown: persist active runs."""
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
