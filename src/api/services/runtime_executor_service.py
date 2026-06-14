"""Core workflow executor service.

This module owns the remaining Agent workflow execution path.  The legacy
runtime module keeps thin compatibility wrappers so older tests and scripts can
still monkeypatch ``api_server`` symbols while the real implementation lives in
an explicit service.
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.agent.engine import TOOLS, agent_loop_stream, get_workdir, run_subagent
from src.agent.file_lock import cleanup_file_lock, get_file_lock
from src.agent.state import WorkflowCancelledError
from src.api.run_state import (
    emit_agent_activity,
    emit_agenthub_event,
    emit_stage_updates,
    get_workspace,
    sync_run_context,
    transition_runtime_state,
)
from src.api.services.change_tracker import ChangeTracker
from src.api.services.intent_router import is_lead_direct_intent
from src.api.services.parallel_agent_service import run_parallel_agent_briefing
from src.api.services.run_finalization_service import (
    cancel_workflow_run,
    complete_workflow_run,
    fail_workflow_run,
    extract_run_memory_best_effort,
    finalize_delivery_best_effort,
    finalize_run_registry,
    persist_terminal_session,
)
from src.api.services.runtime_preparation_service import inject_parallel_briefing, prepare_runtime_system
from src.api.services.runtime_evidence_service import collect_runtime_delivery_evidence
from src.api.services.runtime_registry_service import get_runtime_registry
from src.api.services.runtime_routing_service import execute_lightweight_runtime_route, uses_lightweight_runtime
from src.api.services.runtime_stream_service import make_agent_pool_status_callback, stream_model_response
from src.api.services.runtime_tool_callback_service import RuntimeToolCallbacks
from src.api.services.sse_broker import get_sse_broker
from src.infra.logger import logger
from src.infra.metrics import metrics as metrics_collector
from src.runtime.run_budget import RunBudget
from src.runtime.run_state import RunStatus
from src.runtime.tool_policy_runtime import ToolPolicyRuntime
from src.tools.tool_result import is_tool_error_output, tool_error_message


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_METRICS_HISTORY_FILE = str(PROJECT_ROOT / "metrics_history.json")


@dataclass(slots=True)
class RuntimeExecutorDependencies:
    """Explicit dependencies for the workflow executor.

    Most defaults are production implementations.  Legacy compatibility wrappers
    pass their current module globals here so monkeypatches on ``api_server`` keep
    working during the migration.
    """

    agent_loop_stream: Callable[..., Any] = agent_loop_stream
    run_subagent: Callable[..., Any] = run_subagent
    tools: list[dict[str, Any]] | None = None
    get_workdir: Callable[[], Any] = get_workdir
    get_file_lock: Callable[[str], Any] = get_file_lock
    cleanup_file_lock: Callable[[str], Any] = cleanup_file_lock
    metrics_collector: Any = metrics_collector
    metrics_history_file: str = DEFAULT_METRICS_HISTORY_FILE
    run_parallel_agent_briefing: Callable[..., Any] = run_parallel_agent_briefing
    emit_event: Callable[..., Any] = emit_agenthub_event
    emit_activity: Callable[..., Any] = emit_agent_activity
    emit_stage_updates: Callable[..., Any] = emit_stage_updates
    transition_state: Callable[..., Any] = transition_runtime_state
    sync_run_context: Callable[..., Any] = sync_run_context
    get_workspace: Callable[[], str] = get_workspace
    run_manager: Any | None = None
    active_runs: dict[str, Any] | None = None
    runs_lock: Any | None = None
    event_store: Any | None = None

    def __post_init__(self) -> None:
        registry = get_runtime_registry()
        if self.tools is None:
            self.tools = TOOLS
        if self.run_manager is None:
            self.run_manager = registry.run_manager
        if self.active_runs is None:
            self.active_runs = registry.active_runs
        if self.runs_lock is None:
            self.runs_lock = registry.runs_lock
        if self.event_store is None:
            self.event_store = registry.event_store


def _readonly_subagent_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"read_file", "list_directory", "search_codebase", "project_context", "git_status", "git_diff"}
    return [tool for tool in tools if tool.get("name") in allowed]


def _small_edit_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {
        "read_file",
        "list_directory",
        "search_codebase",
        "project_context",
        "git_status",
        "git_diff",
        "write_file",
        "edit_file",
        "run_tests",
    }
    return [tool for tool in tools if tool.get("name") in allowed]


def _cancel_agent_pool(thread_id: str) -> None:
    try:
        from src.agent.agent_pool import get_pool

        pool = get_pool(thread_id)
        if pool:
            pool.cancel_all()
    except Exception:
        pass


def cancel_agent_pool(thread_id: str) -> None:
    """Cancel all sub-agents for a run."""
    _cancel_agent_pool(thread_id)


def _cleanup_agent_pool(thread_id: str) -> None:
    try:
        from src.agent.agent_pool import cleanup_pool

        cleanup_pool(thread_id)
    except Exception:
        pass


def should_cancel_run(thread_id: str, dependencies: RuntimeExecutorDependencies | None = None) -> bool:
    deps = dependencies or RuntimeExecutorDependencies()
    sm = deps.run_manager.get_state_machine(thread_id)
    if sm and sm.status in {
        RunStatus.CANCELLING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
    }:
        _cancel_agent_pool(thread_id)
        return True
    with deps.runs_lock:
        run_info = deps.active_runs.get(thread_id)
        should_cancel = bool(run_info and run_info.get("status") in {"cancelling", "cancelled", "failed", "interrupted"})
    if should_cancel:
        _cancel_agent_pool(thread_id)
    return should_cancel


def is_simple_lead_message(prompt: str) -> bool:
    return is_lead_direct_intent(prompt)


async def run_readonly_subagent(
    prompt: str,
    system: str,
    agent_type: str,
    tools: list[dict[str, Any]] | None = None,
    dependencies: RuntimeExecutorDependencies | None = None,
) -> str:
    deps = dependencies or RuntimeExecutorDependencies()
    return await deps.run_subagent(
        prompt=prompt,
        system=system,
        agent_type=agent_type,
        tools=tools or _readonly_subagent_tools(deps.tools),
    )


def run_workflow(
    thread_id: str,
    initial_messages: list[Any],
    workspace_dir: str,
    max_retries: int = 3,
    max_coder_steps: int = 15,
    dependencies: RuntimeExecutorDependencies | None = None,
) -> None:
    deps = dependencies or RuntimeExecutorDependencies()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_workflow_async(thread_id, initial_messages, max_retries, max_coder_steps, workspace_dir, deps)
        )
    finally:
        loop.close()
        try:
            deps.run_manager.unregister(thread_id)
        except Exception:
            pass


def run_workflow_from_messages(
    thread_id: str,
    messages: list[dict[str, Any]],
    system: str,
    workspace_dir: str,
    dependencies: RuntimeExecutorDependencies | None = None,
) -> None:
    deps = dependencies or RuntimeExecutorDependencies()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_workflow_async_from_messages(thread_id, messages, system, workspace_dir, deps))
    finally:
        loop.close()
        try:
            deps.run_manager.unregister(thread_id)
        except Exception:
            pass


async def run_workflow_async(
    thread_id: str,
    initial_messages: list[Any],
    max_retries: int,
    max_coder_steps: int,
    workspace_dir: str | None = None,
    dependencies: RuntimeExecutorDependencies | None = None,
) -> None:
    """Async internal implementation of the standard workflow."""
    deps = dependencies or RuntimeExecutorDependencies()

    with deps.runs_lock:
        run_info = deps.active_runs.get(thread_id)
        if not run_info:
            return
        workspace_dir = workspace_dir or run_info.get("workspace_dir")
        execution_plan = run_info.get("execution_plan", {})
        run_team = run_info.get("team", [])
        conversation_id = run_info.get("conversation_id")
        intent_decision = execution_plan.get("intent_decision", {}) if isinstance(execution_plan.get("intent_decision"), dict) else {}
        is_lead_direct_run = intent_decision.get("execution_route") == "lead_direct_reply"
        intent_route = str(intent_decision.get("route") or "")
        uses_runtime_turn_loop = uses_lightweight_runtime(intent_route)

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
        change_tracker = ChangeTracker(thread_id, workspace_dir or deps.get_workspace())
        current_run = deps.active_runs.get(thread_id)
        if current_run:
            current_run["change_tracker"] = change_tracker
    workspace_dir = workspace_dir or deps.get_workspace()

    messages = [{"role": m.type if hasattr(m, "type") else "user", "content": m.content} for m in initial_messages]

    system = prepare_runtime_system(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        messages=messages,
        execution_plan=execution_plan,
        run_team=run_team,
        conversation_id=conversation_id,
        is_lead_direct_run=is_lead_direct_run,
        uses_runtime_turn_loop=uses_runtime_turn_loop,
        workdir=str(deps.get_workdir()),
        event_store=deps.event_store,
        emit_event=deps.emit_event,
        emit_activity=deps.emit_activity,
    )

    run_input_tokens = 0
    run_output_tokens = 0
    tool_callbacks = RuntimeToolCallbacks(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        policy_runtime=policy_runtime,
        change_tracker=change_tracker,
        active_runs=deps.active_runs,
        runs_lock=deps.runs_lock,
        metrics_collector=deps.metrics_collector,
        emit_event=deps.emit_event,
        emit_activity=deps.emit_activity,
        transition_state=deps.transition_state,
        sync_run_context=deps.sync_run_context,
        emit_stage_updates=deps.emit_stage_updates,
        should_cancel=lambda run_id: should_cancel_run(run_id, deps),
        token_metrics=lambda: (run_input_tokens, run_output_tokens),
        uses_runtime_turn_loop=uses_runtime_turn_loop,
    )
    on_tool_check = tool_callbacks.on_tool_check
    on_tool_call = tool_callbacks.on_tool_call
    runtime_tool_evidence = tool_callbacks.evidence

    final_status = "completed"

    try:
        if should_cancel_run(thread_id, deps):
            raise WorkflowCancelledError("Agent 运行已取消")
        await inject_parallel_briefing(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            messages=messages,
            execution_plan=execution_plan,
            uses_runtime_turn_loop=uses_runtime_turn_loop,
            briefing_runner=deps.run_parallel_agent_briefing,
            subagent_runner=lambda *args, **kwargs: run_readonly_subagent(*args, dependencies=deps, **kwargs),
            emit_event=deps.emit_event,
            emit_activity=deps.emit_activity,
            readonly_tools=_readonly_subagent_tools(deps.tools),
        )

        if should_cancel_run(thread_id, deps):
            raise WorkflowCancelledError("Agent 运行已取消")

        def _on_llm_response(input_tokens: int, output_tokens: int) -> None:
            nonlocal run_input_tokens, run_output_tokens
            run_input_tokens += input_tokens
            run_output_tokens += output_tokens
            deps.emit_event(
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

        deps.emit_activity(
            thread_id=thread_id,
            agent="lead",
            title="Lead 进入主循环",
            content="正在根据任务复杂度决定直接回答、修改文件、运行检查或创建临时 Agent。",
            workspace_dir=workspace_dir,
            payload={"phase": "agent_loop", "can_cancel": True},
        )
        try:
            from src.api.services.agent_loop_state_service import append_loop_step

            if not is_lead_direct_run and not uses_runtime_turn_loop:
                append_loop_step(
                    thread_id,
                    workspace_dir,
                    phase="decide",
                    action={
                        "type": "create_tasks",
                        "goal": "Enter the Lead loop and decide the next action from context.",
                        "agent": "Lead",
                    },
                    summary="Lead loop started.",
                )
        except Exception:
            pass

        token_broker = get_sse_broker()
        token_counter = 0

        agent_pool_status_callback = make_agent_pool_status_callback(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            emit_event=deps.emit_event,
        )

        async def _stream_model_response(
            toolset: list[dict[str, Any]],
            turn_context: dict[str, Any] | None = None,
        ) -> str:
            nonlocal token_counter
            stream_result = await stream_model_response(
                thread_id=thread_id,
                workspace_dir=workspace_dir,
                messages=messages,
                base_system=system,
                tools=toolset,
                agent_loop_stream=deps.agent_loop_stream,
                token_broker=token_broker,
                token_counter=token_counter,
                turn_context=turn_context,
                uses_runtime_turn_loop=uses_runtime_turn_loop,
                intent_route=intent_route,
                on_tool_check=on_tool_check,
                on_tool_call=on_tool_call,
                on_cancel_check=lambda: should_cancel_run(thread_id, deps),
                runtime_context={
                    "thread_id": thread_id,
                    "workspace_dir": workspace_dir,
                    "conversation_id": conversation_id,
                    "agent": "Lead",
                    "file_lock": deps.get_file_lock(thread_id),
                    "pool_status_callback": agent_pool_status_callback,
                },
                on_llm_response=_on_llm_response,
            )
            token_counter = stream_result.token_counter
            if is_tool_error_output(stream_result.text):
                raise RuntimeError(tool_error_message(stream_result.text))
            return stream_result.text

        if uses_runtime_turn_loop:
            result = await execute_lightweight_runtime_route(
                thread_id=thread_id,
                workspace_dir=workspace_dir,
                intent_route=intent_route,
                stream_turn=_stream_model_response,
                readonly_tools=_readonly_subagent_tools(deps.tools),
                small_edit_tools=_small_edit_tools(deps.tools),
                tool_evidence=runtime_tool_evidence,
                sync_run_context=deps.sync_run_context,
            )
        else:
            result = await _stream_model_response(deps.tools)

        if is_tool_error_output(result):
            raise RuntimeError(tool_error_message(result))
        if (
            not uses_runtime_turn_loop
            and not is_lead_direct_run
            and bool(intent_decision.get("requires_workspace_write"))
        ):
            deps.sync_run_context(thread_id=thread_id, workspace_dir=workspace_dir)
            evidence = collect_runtime_delivery_evidence(
                thread_id,
                workspace_dir,
                tool_calls=runtime_tool_evidence,
            )
            if not evidence.has_changes:
                raise RuntimeError(
                    "本轮任务要求创建或修改工作区文件，但未检测到真实文件变更，不能标记为完成。"
                )
        complete_workflow_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            result=result,
            messages=messages,
            uses_runtime_turn_loop=uses_runtime_turn_loop,
            active_runs=deps.active_runs,
            runs_lock=deps.runs_lock,
            event_store=deps.event_store,
            emit_event=deps.emit_event,
            sync_run_context=deps.sync_run_context,
            emit_stage_updates=deps.emit_stage_updates,
        )

    except WorkflowCancelledError:
        final_status = "cancelled"
        cancel_workflow_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            messages=messages,
            active_runs=deps.active_runs,
            runs_lock=deps.runs_lock,
            event_store=deps.event_store,
            emit_event=deps.emit_event,
            sync_run_context=deps.sync_run_context,
            emit_stage_updates=deps.emit_stage_updates,
        )
    except Exception as e:
        final_status = "failed"
        error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error("run_workflow_async 工作流异常: %s", e, exc_info=True)
        fail_workflow_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            error=e,
            error_detail=error_detail,
            messages=messages,
            active_runs=deps.active_runs,
            runs_lock=deps.runs_lock,
            event_store=deps.event_store,
            emit_event=deps.emit_event,
            sync_run_context=deps.sync_run_context,
            emit_stage_updates=deps.emit_stage_updates,
        )
    finally:
        try:
            deps.metrics_collector.flush_to_file()
            deps.metrics_collector.append_to_history(deps.metrics_history_file, tag=thread_id[:8])
        except Exception:
            pass
        finalize_delivery_best_effort(thread_id, workspace_dir)
        finalize_run_registry(
            active_runs=deps.active_runs,
            runs_lock=deps.runs_lock,
            run_manager=deps.run_manager,
            thread_id=thread_id,
            final_status=final_status,
        )
        _cleanup_agent_pool(thread_id)
        try:
            deps.cleanup_file_lock(thread_id)
        except Exception:
            pass


async def run_workflow_async_from_messages(
    thread_id: str,
    messages: list[dict[str, Any]],
    system: str,
    workspace_dir: str,
    dependencies: RuntimeExecutorDependencies | None = None,
) -> None:
    """Resume a run with pre-built messages and system prompt."""
    deps = dependencies or RuntimeExecutorDependencies()
    final_status = "completed"
    token_broker = get_sse_broker()
    token_counter = 0

    def _on_llm_response(input_tokens: int, output_tokens: int) -> None:
        deps.emit_event(
            thread_id=thread_id,
            event_type="metrics_updated",
            title="Token 指标更新",
            content=f"in={input_tokens} out={output_tokens}",
            agent="lead",
            payload={"total_input_tokens": input_tokens, "total_output_tokens": output_tokens},
            workspace_dir=workspace_dir,
        )

    try:
        agent_pool_status_callback = make_agent_pool_status_callback(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            emit_event=deps.emit_event,
        )
        resume_session = (
            deps.event_store.get_session(thread_id, workspace_dir) or {}
            if hasattr(deps.event_store, "get_session")
            else {}
        )
        stream_result = await stream_model_response(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            messages=messages,
            base_system=system,
            tools=deps.tools,
            agent_loop_stream=deps.agent_loop_stream,
            token_broker=token_broker,
            token_counter=token_counter,
            on_cancel_check=lambda: should_cancel_run(thread_id, deps),
            on_llm_response=_on_llm_response,
            runtime_context={
                "thread_id": thread_id,
                "workspace_dir": workspace_dir,
                "conversation_id": resume_session.get("conversation_id"),
                "agent": "Lead",
                "file_lock": deps.get_file_lock(thread_id),
                "pool_status_callback": agent_pool_status_callback,
            },
        )
        result = stream_result.text
        token_counter = stream_result.token_counter

        deps.emit_event(
            thread_id=thread_id,
            event_type="assistant_message",
            title="Agent 回复",
            content=result[:5000],
            agent="lead",
            payload={"content": result, "token_counter": token_counter},
            workspace_dir=workspace_dir,
        )
        deps.emit_event(
            thread_id=thread_id,
            event_type="done",
            title="任务完成",
            content="Agent 运行已完成",
            agent="lead",
            payload={"status": "completed"},
            workspace_dir=workspace_dir,
        )
        persist_terminal_session(
            event_store=deps.event_store,
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="completed",
            summary=result[:2000],
            execution_summary=result[:1200],
            saved_messages=messages,
        )
        extract_run_memory_best_effort(thread_id, workspace_dir)

    except WorkflowCancelledError:
        final_status = "cancelled"
        deps.emit_event(
            thread_id=thread_id,
            event_type="done",
            title="任务已取消",
            content="Agent 运行已取消",
            agent="lead",
            payload={"status": "cancelled"},
            workspace_dir=workspace_dir,
        )
        persist_terminal_session(
            event_store=deps.event_store,
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="cancelled",
            summary="Agent 运行已取消",
            saved_messages=messages,
        )

    except Exception as e:
        final_status = "failed"
        error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error("run_workflow_async_from_messages 工作流异常: %s", e, exc_info=True)
        deps.emit_event(
            thread_id=thread_id,
            event_type="error",
            title="运行异常",
            content=str(e),
            agent="lead",
            payload={"error": str(e), "detail": error_detail},
            workspace_dir=workspace_dir,
        )
        persist_terminal_session(
            event_store=deps.event_store,
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            status="failed",
            error=str(e),
            saved_messages=messages,
        )
        extract_run_memory_best_effort(thread_id, workspace_dir)

    finally:
        finalize_run_registry(
            active_runs=deps.active_runs,
            runs_lock=deps.runs_lock,
            run_manager=deps.run_manager,
            thread_id=thread_id,
            final_status=final_status,
        )
        try:
            deps.cleanup_file_lock(thread_id)
        except Exception:
            pass


__all__ = [
    "RuntimeExecutorDependencies",
    "cancel_agent_pool",
    "is_simple_lead_message",
    "run_readonly_subagent",
    "run_workflow",
    "run_workflow_async",
    "run_workflow_async_from_messages",
    "run_workflow_from_messages",
    "should_cancel_run",
]
