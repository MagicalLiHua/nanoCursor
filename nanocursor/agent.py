from __future__ import annotations

import asyncio
import copy
from contextlib import aclosing, nullcontext
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from nanocursor.events import (
    StreamText,
    ThinkingText,
    RetryEvent,
    ToolUseEvent,
    ToolResultEvent,
    TurnComplete,
    LoopComplete,
    UsageEvent,
    ErrorEvent,
    AgentRunError,
    CompactNotification,
    MemoryContextChanged,
    HookEvent,
    PermissionResponse,
    PermissionRequest,
    PermissionCall,
    AgentEvent,
)
from nanocursor.client import LLMClient, LLMError
from nanocursor.recovery import RecoveryError, RecoveryRequired, current_runtime as recovery_runtime
from nanocursor.context import (
    CompactBoundary,
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementRecord,
    ContentReplacementState,
    RecoveryState,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    compute_compact_threshold,
    create_replacement_state,
    ensure_session_dir,
    load_replacement_records,
    reconstruct_replacement_state,
)
from nanocursor.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from nanocursor.conversation import ThinkingBlock as ConvThinkingBlock
from nanocursor.memory.auto_memory import MemoryManager
from nanocursor.permissions import (
    PermissionChecker,
    PermissionMode,
)
from nanocursor.permissions.approval_context import build_request
from nanocursor.permissions.reviewer import ApprovalController
from nanocursor.hooks import HookContext, HookEngine
from nanocursor.hooks.engine import HookNotification
from nanocursor.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from nanocursor.tools import ToolRegistry
from nanocursor.tools.runtime import ToolRuntimeContext, bind_runtime, current_runtime, normalize_local_arguments
from nanocursor.tools.base import (
    MAX_OUTPUT_CHARS,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

log = logging.getLogger(__name__)

MEMORY_EXTRACTION_INTERVAL = 1
MAX_OUTPUT_TOKENS_RECOVERIES = 3


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    usage_available: bool = False


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        ended = False
        async with aclosing(stream):
            async for event in stream:
                if ended:
                    raise LLMError("Model stream contained data after its terminal event")
                if isinstance(event, TextDelta):
                    self.response.text += event.text
                    yield StreamText(text=event.text)
                elif isinstance(event, ThinkingDelta):
                    yield ThinkingText(text=event.text)
                elif isinstance(event, ThinkingComplete):
                    self.response.thinking_blocks.append(
                        ThinkingBlock(thinking=event.thinking, signature=event.signature)
                    )
                elif isinstance(event, ToolCallStart):
                    pass
                elif isinstance(event, ToolCallDelta):
                    pass
                elif isinstance(event, ToolCallComplete):
                    self.response.tool_calls.append(event)
                    yield ToolUseEvent(
                        tool_name=event.tool_name,
                        tool_id=event.tool_id,
                        arguments=event.arguments,
                    )
                elif isinstance(event, StreamEnd):
                    if event.stop_reason not in {"end_turn", "stop_sequence", "tool_use", "max_tokens", "stop", "tool_calls"}:
                        raise LLMError(f"Model stream ended unsuccessfully: {event.stop_reason}")
                    ended = True
                    self.response.stop_reason = event.stop_reason
                    self.response.input_tokens = event.input_tokens
                    self.response.output_tokens = event.output_tokens
                    self.response.cache_read = event.cache_read
                    self.response.cache_creation = event.cache_creation
                    self.response.usage_available = event.usage_available
        if not ended:
            raise LLMError("Model stream ended without a terminal event")


# ---------------------------------------------------------------------------
# tool 批量执行
# ---------------------------------------------------------------------------

@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = tool is not None and tool.is_concurrency_safe and registry.is_enabled(tc.tool_name)

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# streaming 执行器 — 在 LLM streaming 期间启动 tool 执行
# ---------------------------------------------------------------------------

@dataclass
class _ToolExecResult:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float
    is_unknown: bool


class StreamingExecutor:
    """Ordered safety barriers, permission events, and owned cancellation."""
    def __init__(self) -> None:
        self._tasks: list[tuple[ToolCallComplete, asyncio.Task[_ToolExecResult]]] = []
        self._barrier: asyncio.Task | None = None
        self._permissions: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self._approval_lock = asyncio.Lock()

    def submit(self, call: ToolCallComplete, run: Callable[[], Awaitable[_ToolExecResult]],
               concurrent: bool = False) -> None:
        dependencies = ([self._barrier] if self._barrier else []) if concurrent else [t for _, t in self._tasks]

        async def execute() -> _ToolExecResult:
            if dependencies:
                await asyncio.gather(*(asyncio.shield(t) for t in dependencies))
            return await run()

        task = asyncio.create_task(execute())
        self._tasks.append((call, task))
        if not concurrent:
            self._barrier = task

    async def request_permission(self, call: ToolCallComplete) -> PermissionResponse:
        async with self._approval_lock:
            future = asyncio.get_running_loop().create_future()
            await self._permissions.put(PermissionRequest(
                call.tool_name, PermissionChecker.describe_tool_action(call.tool_name, call.arguments), future,
                getattr(call, "approval_reason", ""), getattr(call, "approval_cwd", ""),
                getattr(call, "allow_always", True), getattr(call, "allow_edits", False)))
            return await future

    async def iter_results(self) -> AsyncIterator[PermissionRequest | _ToolExecResult]:
        for call, task in self._tasks:
            while not task.done():
                waiter = asyncio.create_task(self._permissions.get())
                try:
                    await asyncio.wait((task, waiter), return_when=asyncio.FIRST_COMPLETED)
                    if waiter.done():
                        request = waiter.result()
                        if not request.future.done():
                            yield request
                finally:
                    if not waiter.done():
                        waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
            yield self._result(call, task)

    @staticmethod
    def _result(call: ToolCallComplete, task: asyncio.Task) -> _ToolExecResult:
        if task.cancelled():
            result = ToolResult("Tool execution cancelled; any completed side effects were not rolled back.", True)
        elif isinstance(task.exception(), RecoveryError):
            raise task.exception()
        elif task.exception() is not None:
            result = ToolResult(f"Tool execution error: {task.exception()}", True)
        else:
            return task.result()
        return _ToolExecResult(call.tool_id, call.tool_name, result, 0.0, False)

    async def cancel_and_wait(self) -> list[_ToolExecResult]:
        for _, task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*(task for _, task in self._tasks), return_exceptions=True)
        results = []
        for call, task in self._tasks:
            try:
                results.append(self._result(call, task))
            except RecoveryError as exc:
                results.append(_ToolExecResult(call.tool_id, call.tool_name,
                    ToolResult(f"Execution stopped by recovery protection: {exc}", True, outcome_unknown=True), 0.0, False))
        return results


@dataclass
class PendingToolTurn:
    collector: StreamCollector
    executor: StreamingExecutor
    committed: bool = False


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------

class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 0,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
        system_prompt_override: str | None = None,
        inject_environment_context: bool = True,
        spawn_allowed: bool = True,
        sandbox_root: str | None = None,
        approval_controller: ApprovalController | None = None,
        session_work_dir: str | None = None,
        memory_recall_config: Any = None,
        execution_guard: Callable[[], str | None] | None = None,
        recovery: Any = None,
    ) -> None:
        self.execution_guard = execution_guard
        self.recovery = recovery
        self.client = client
        self.registry = registry
        self.protocol = protocol
        self.work_dir = str(Path(work_dir).resolve())
        self._spawn_allowed = spawn_allowed
        self.sandbox_root = str(Path(sandbox_root).resolve()) if sandbox_root else None
        self._file_versions: dict[str, Any] = {}
        self._turn_expected_versions: dict[str, Any] | None = None
        self._pending_tool_turn: PendingToolTurn | None = None
        self.worktree_cleanup: Callable[[], Awaitable[str]] | None = None
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.approval_controller = approval_controller
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        self.context_window = context_window
        self.session_work_dir = str(Path(session_work_dir or work_dir).resolve())
        self.session_dir = ensure_session_dir(self.session_work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        self.replacement_state: ContentReplacementState = create_replacement_state()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.usage_missing_requests = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        from nanocursor.memory.recall import MemoryRecallService
        self.memory_recall = MemoryRecallService(memory_recall_config)
        self.hook_engine = hook_engine
        self.system_prompt_override = system_prompt_override
        self.inject_environment_context = inject_environment_context
        self._loop_count = 0
        # 记忆提取合并策略：
        # _extracting: 标记是否有提取正在进行
        # _pending_extraction: 提取期间又触发了新请求，标记需要尾随提取
        self._extracting = False
        self._pending_extraction = False
        self.background_task_callback: Callable[[asyncio.Task], None] | None = None
        self._memory_tasks: set[asyncio.Task] = set()
        self.session_id: str = ""
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        self.coordinator_mode: bool = False
        self.team_name: str = ""
        self._team_manager: Any = None
        self.notification_fn: Callable[[], list[str]] | None = None
        self.file_history: Any = None

        # Prepared by the interactive owner, consumed before the first request.
        self.memory_recall_task: Any | None = None

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(Path(self.session_work_dir) / ".nanocursor" / "sessions" / f"{self.session_id}.jsonl")
        return ""

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    _plan_path_cache: Path | None = None

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        import random
        import datetime
        _ADJECTIVES = ["bold", "bright", "calm", "cool", "deep", "fair", "fast", "fine",
                       "glad", "keen", "kind", "lean", "mild", "neat", "pure", "safe",
                       "slim", "soft", "tall", "warm", "wise", "grand", "swift", "vivid"]
        _NOUNS = ["sketch", "draft", "spark", "bloom", "trail", "ridge", "creek", "grove",
                  "cliff", "cloud", "field", "forge", "frost", "haven", "pearl", "stone",
                  "storm", "river", "tower", "delta", "flame", "orbit", "pulse", "shore"]
        plans_dir = Path(self.work_dir) / ".nanocursor" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        changed = mode != self.permission_mode
        if self.approval_controller and changed:
            self.approval_controller.revision += 1
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode
        callback = getattr(self, "on_permission_mode_changed", None)
        if changed and callback:
            callback()

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog


    def set_agent_catalog(self, catalog: str, catalog_list: list[tuple[str, str]] | None = None) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def _build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=kwargs.get("tool_args", {}),
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
        )

    async def _run_hooks(self, event: str, context: HookContext) -> None:
        ledger = self.recovery or recovery_runtime()
        with ledger.activate() if ledger else nullcontext():
            with bind_runtime(ToolRuntimeContext(cwd=Path(self.work_dir), agent_id=self.agent_id,
                                                 sandbox_root=Path(self.sandbox_root) if self.sandbox_root else None,
                                                 spawn_allowed=self.spawn_allowed, file_history=self.file_history)):
                await self.hook_engine.run_hooks(event, context)

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def _drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    @property
    def spawn_allowed(self) -> bool:
        return self._spawn_allowed

    @property
    def plan_path(self) -> Path:
        return self._get_plan_path()

    def set_team_manager(self, manager) -> None:
        self._team_manager = manager

    async def run_hooks(self, event: str, context: HookContext) -> None:
        await self._run_hooks(event, context)

    async def extract_memories(self, conversation: ConversationManager) -> None:
        await self._extract_memories(conversation)

    def reset_usage(self) -> None:
        self._loop_count = 0
        self.clear_active_skills()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.usage_missing_requests = 0

    def clear_file_versions(self) -> None:
        self._file_versions.clear()

    def reset_history_state(self) -> None:
        self.replacement_state = create_replacement_state()
        self.recovery_state = RecoveryState()
        self.clear_active_skills()

    def bind_session(self, session, file_history) -> None:
        from nanocursor.memory.recall import RecallOutcome

        self._loop_count = 0
        self.memory_recall.invalidate()
        self.memory_recall.last = RecallOutcome()
        self.memory_recall.context_tokens = 0
        self.memory_recall.injected = self.memory_recall.deduplicated = 0
        self.session_id = session.session_id
        self.file_history = file_history
        for tool in self.registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = file_history
        self.clear_file_versions()
        self.reset_history_state()
        if self.approval_controller:
            self.approval_controller.authorization = session.load_approval_context()
            self.approval_controller.revision += 1
            self.approval_controller.persist_authorization()

    def synchronize_memory_context(self, conversation: ConversationManager) -> None:
        from nanocursor.memory.context import owned
        from nanocursor.memory.budget import estimate

        self.memory_recall.context_tokens = sum(estimate(message.content) for message in conversation.history if owned(message))

    def set_work_dir(self, work_dir: str, *, isolated: bool = False) -> None:
        from nanocursor.permissions import PathSandbox
        if self.approval_controller:
            self.approval_controller.revision += 1
        if self.recovery:
            self.recovery = self.recovery.switch(work_dir)
        self.work_dir = str(Path(work_dir).resolve())
        self.sandbox_root = self.work_dir if isolated else None
        self._file_versions.clear()
        if self.permission_checker:
            self.permission_checker._session_allowed.clear()
            self.permission_checker.sandbox = PathSandbox(self.work_dir)
        callback = getattr(self, "on_work_dir_changed", None)
        if callback:
            callback(self.work_dir)

    async def run(self, conversation: ConversationManager, *, interactive: bool = True, source: str = "user") -> AsyncIterator[AgentEvent]:
        runtime = self.recovery or recovery_runtime()
        if runtime is not None:
            runtime = runtime.child(self.work_dir)
            self.recovery = runtime
            if self.file_history is None or self.file_history.workspace_id != runtime.workspace_id:
                from nanocursor.filehistory import FileHistory
                self.file_history = runtime.file_history or FileHistory(
                    self.work_dir, self.session_id or runtime.session_id or self.agent_id,
                    store=runtime.store, workspace_id=runtime.workspace_id, generation=runtime.generation)
                runtime.file_history = self.file_history
        with runtime.activate() if runtime else nullcontext():
            run_id = None
            state = "interrupted"
            try:
                if runtime:
                    run_id = runtime.begin_run(self.session_id or None,
                                               source="subagent" if self.parent_id else source)
                    # Child conversations are not appended to the parent's transcript.
                    if not self.parent_id and conversation.history:
                        runtime.persist_message(conversation.history[-1])
                if self.file_history is not None and source != "notification" and (not self.parent_id or not self.file_history.has_snapshots()):
                    latest = conversation.history[-1] if conversation.history else None
                    self.file_history.begin_checkpoint(
                        max(0, len(conversation.history) - 1),
                        latest.content if latest and latest.role == "user" else "Agent run",
                        conversation=conversation.history[:-1], run_id=run_id,
                        generation=runtime.generation if runtime else None,
                        env_injected=conversation.env_injected, ltm_injected=conversation.ltm_injected,
                    )
                async for event in self._run(conversation, interactive=interactive):
                    yield event
                state = "completed"
            except RecoveryError as exc:
                yield ErrorEvent(str(exc), code="recovery_required" if isinstance(exc, RecoveryRequired) else "recovery_storage_error")
            finally:
                # Always settle owned tasks, even when the consumer cancels a stream.
                cleanup = asyncio.create_task(self._close_pending_tool_turn(conversation))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                cleanup.result()
                self._turn_expected_versions = None
                if runtime and run_id:
                    runtime.end_run(run_id, state=state)

    def _persist_message(self, message) -> None:
        runtime = self.recovery or recovery_runtime()
        if runtime:
            if self.parent_id:
                runtime.persist_child_message(message, self.agent_id)
            else:
                runtime.persist_message(message)

    async def _close_pending_tool_turn(self, conversation: ConversationManager) -> None:
        pending = self._pending_tool_turn
        if pending is None:
            return
        results = await pending.executor.cancel_and_wait()
        response = pending.collector.response
        if response.tool_calls and pending.committed:
            by_id = {r.tool_id: r for r in results}
            ledger = self.recovery or recovery_runtime()
            if ledger:
                for tc in response.tool_calls:
                    known = ledger.result_record(tc.tool_id)
                    if known:
                        by_id[tc.tool_id] = _ToolExecResult(tc.tool_id, tc.tool_name,
                            ToolResult(known["content"], known["is_error"]), 0.0, False)
            conversation.add_tool_results_message([
                ToolResultBlock(tc.tool_id,
                    self._maybe_persist_or_truncate(tc.tool_id, by_id[tc.tool_id].result.output)
                    if tc.tool_id in by_id else "Interrupted before dispatch; this call was not executed or replayed.",
                    by_id[tc.tool_id].result.is_error if tc.tool_id in by_id else True)
                for tc in response.tool_calls
            ])
            self._persist_message(conversation.history[-1])
        self._pending_tool_turn = None

    async def _run(self, conversation: ConversationManager, *, interactive: bool) -> AsyncIterator[AgentEvent]:
        if self.recovery:
            self.recovery.ensure_ready()
        self._current_conversation = conversation
        from nanocursor.memory.context import sync_memory
        from nanocursor.memory.recall import RecallOutcome
        recall = RecallOutcome()
        before_injection = copy.deepcopy(conversation)
        env_context = ""
        if self.inject_environment_context:
            env_context = build_environment_context(
                self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
            )
            conversation.inject_environment(env_context)

        conversation.inject_long_term_memory(self.instructions_content, "")
        if self.memory_manager:
            if self.memory_recall_task is not None:
                recall = await self.memory_recall_task
            else:
                # Other Agent callers receive bounded indexes, not automatic
                # query/model recall reserved for the main interactive entry.
                recall = await self.memory_recall.prepare("", self.memory_manager.user_mem_dir,
                                                         self.memory_manager.project_mem_dir)
            sync_memory(conversation, recall, self.memory_recall, self.context_window,
                        getattr(self.client, "max_output_tokens", 0))
            if conversation.history != before_injection.history:
                yield MemoryContextChanged(before_injection)

        if self.hook_engine:
            ctx = self._build_hook_context("session_start")
            await self._run_hooks("session_start", ctx)
            for he in self._drain_hook_events():
                yield he

        iteration = 0
        consecutive_unknown = 0
        parameter_error_turns = 0
        output_recoveries = 0

        while True:
            if self.execution_guard and (reason := self.execution_guard()):
                yield ErrorEvent(message=reason, code="authority_changed")
                break
            iteration += 1

            if self.max_iterations > 0 and iteration > self.max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self.max_iterations})", code="max_iterations"
                )
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self._run_hooks("turn_start", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            if self.hook_engine:
                ctx = self._build_hook_context("pre_send")
                await self._run_hooks("pre_send", ctx)
                for he in self._drain_hook_events():
                    yield he

            hook_prompts = (
                self.hook_engine.get_prompt_messages() if self.hook_engine else None
            )
            system = self.system_prompt_override or build_system_prompt(
                hook_prompts=hook_prompts,
                coordinator_mode=self.coordinator_mode,
                agent_catalog=self._agent_catalog_list or None,
            )

            if self.plan_mode and self.spawn_allowed:
                plan_path = str(self._get_plan_path())
                if self.permission_checker:
                    self.permission_checker.plan_file_path = plan_path
                plan_exists = self._get_plan_path().exists()
                plan_reminder = build_plan_mode_reminder(
                    plan_path, plan_exists, iteration
                )
                conversation.add_system_reminder(plan_reminder)

            if self.hook_engine:
                for note in self.hook_engine.drain_notifications():
                    conversation.add_system_reminder(
                        f"Hook [{note.hook_id}] {note.event}: {note.output}"
                    )

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            tools = self.registry.get_all_schemas(self.protocol)

            # Layer 1: apply tool-result budget（就地修改 conversation）
            new_records = apply_tool_result_budget(
                conversation, self.session_dir, self.replacement_state
            )
            if new_records:
                append_replacement_records(self.session_dir, new_records)

            # Layer 2: 接近 context window 上限时自动 compact
            # tool-result budget 已就地修改 conversation，直接用 conversation.history 估算
            prior_conversation = (
                copy.deepcopy(conversation)
                if conversation.current_tokens() >= compute_compact_threshold(self.context_window)
                else None
            )
            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                tool_schemas=self.registry.get_all_schemas(self.protocol),
                transcript_path=self._transcript_path,
            )
            after_compact = None
            if isinstance(compact_result, CompactEvent):
                after_compact = copy.deepcopy(conversation)
                yield CompactNotification(
                    before_tokens=compact_result.before_tokens,
                    message=f"上下文已压缩（压缩前 {compact_result.before_tokens:,} tokens）",
                    boundary=compact_result.boundary,
                    prior_conversation=prior_conversation,
                )
                if env_context:
                    conversation.inject_environment(env_context)
                conversation.inject_long_term_memory(self.instructions_content, "")
                # 压缩后重新应用 budget（就地修改）
                apply_tool_result_budget(
                    conversation, self.session_dir, self.replacement_state
                )
            elif isinstance(compact_result, str):
                fatal = conversation.current_tokens() >= compute_compact_threshold(self.context_window, manual=True)
                yield ErrorEvent(message=compact_result, fatal=fatal, code="compact_failed")
                if fatal:
                    break

            if self.memory_manager:
                prior_memory = after_compact or copy.deepcopy(conversation)
                changed = sync_memory(conversation, recall, self.memory_recall, self.context_window,
                                      getattr(self.client, "max_output_tokens", 0), record_stats=False)
                if changed or prior_memory.history != conversation.history:
                    yield MemoryContextChanged(prior_memory)

            # A short but oversized tail can leave no prefix worth
            # summarizing. None (or a summary that is still too large) must
            # never authorize another request beyond the hard input budget.
            if conversation.current_tokens() >= compute_compact_threshold(self.context_window, manual=True):
                yield ErrorEvent(
                    message="上下文仍超过安全上限，已保留当前会话；请减少输入或手动整理后重试。",
                    fatal=True, code="compact_failed",
                )
                break

            if self.recovery:
                self.recovery.ensure_ready()
            collector = StreamCollector()
            executor = StreamingExecutor()
            self._pending_tool_turn = PendingToolTurn(collector, executor)
            self._turn_expected_versions = dict(self._file_versions)
            llm_stream = self.client.stream(conversation, system=system, tools=tools)
            async for event in collector.consume(llm_stream):
                yield event

            response = collector.response
            conv_thinking = [ConvThinkingBlock(tb.thinking, tb.signature) for tb in response.thinking_blocks]
            conversation.add_assistant_message(response.text, [
                ToolUseBlock(tc.tool_id, tc.tool_name, tc.arguments) for tc in response.tool_calls
            ], thinking_blocks=conv_thinking)
            self._persist_message(conversation.history[-1])
            self._pending_tool_turn.committed = bool(response.tool_calls)

            if self.hook_engine:
                ctx = self._build_hook_context("post_receive", message=response.text)
                await self._run_hooks("post_receive", ctx)
                for he in self._drain_hook_events():
                    yield he

            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            if not response.usage_available:
                self.usage_missing_requests += 1
            yield UsageEvent(
                input_tokens=self.total_input_tokens,
                output_tokens=self.total_output_tokens,
            )

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens" and not response.tool_calls:
                if output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                    output_recoveries += 1
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces."
                    )
                    self._persist_message(conversation.history[-1])
                    yield RetryEvent(
                        reason=f"max_tokens recovery {output_recoveries}/{MAX_OUTPUT_TOKENS_RECOVERIES}"
                    )
                    continue
                else:
                    yield ErrorEvent("Agent stopped after exhausting output-token recovery", code="output_limit")
                    break
            else:
                output_recoveries = 0

            if not response.tool_calls:
                self._pending_tool_turn = None
                # Final replies also become part of the current context. Anchor
                # their usage before LoopComplete exposes the finished turn.
                conversation.record_usage_anchor(
                    response.input_tokens,
                    response.output_tokens,
                    response.cache_read,
                    response.cache_creation,
                )
                self._loop_count += 1
                if (
                    self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0
                    and self.memory_manager
                ):
                    from copy import deepcopy
                    task = asyncio.create_task(self._extract_memories(deepcopy(conversation)))
                    self._memory_tasks.add(task)
                    task.add_done_callback(self._memory_tasks.discard)
                    if self.background_task_callback:
                        self.background_task_callback(task)
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self._run_hooks("turn_end", ctx)
                    ctx = self._build_hook_context("session_end")
                    await self._run_hooks("session_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                yield LoopComplete(total_turns=iteration)
                break

            for tc in response.tool_calls:
                tool = self.registry.get(tc.tool_name)
                approval = executor.request_permission if interactive else None
                operation_id = self.recovery.begin_operation("tool", tc.tool_name, tc.arguments,
                    cwd=self.work_dir, tool_call_id=tc.tool_id, state="planned") if self.recovery else None
                executor.submit(tc, lambda tc=tc, operation_id=operation_id: self._execute_single_tool_direct(tc, approval, operation_id),
                                concurrent=bool(tool and tool.is_concurrency_safe and tool.is_read_only))
            # 在 assistant 回复加入历史后锚定实际用量：基线（input + cache + output）
            # 覆盖到当前位置，因此下一轮迭代顶部的 auto-compact 检查只需对
            # 接下来追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            # Tool execution starts only after the full assistant envelope is durable.
            tool_results: list[ToolResultBlock] = []
            async for item in executor.iter_results():
                if isinstance(item, PermissionRequest):
                    yield item
                    continue
                br = item
                consecutive_unknown = consecutive_unknown + 1 if br.is_unknown else 0
                content = self._maybe_persist_or_truncate(br.tool_id, br.result.output)
                tool_results.append(ToolResultBlock(br.tool_id, content, br.result.is_error))
                yield ToolResultEvent(br.tool_id, br.tool_name, content, br.result.is_error, br.elapsed)

            conversation.add_tool_results_message(tool_results)
            self._persist_message(conversation.history[-1])
            self._pending_tool_turn = None
            parameter_error_turns = parameter_error_turns + 1 if any(
                r.is_error and r.content.startswith(("Parameter JSON error:", "Parameter validation error:"))
                for r in tool_results
            ) else 0
            if parameter_error_turns >= 3:
                yield ErrorEvent(message="Agent stopped after three consecutive turns with invalid tool arguments", code="invalid_tool_arguments")
                break
            if consecutive_unknown >= 3:
                yield ErrorEvent(
                    message="Agent terminated: too many consecutive unknown tool calls", code="unknown_tools"
                )
                break

            exit_plan_called = any(
                tc.tool_name == "ExitPlanMode" for tc in response.tool_calls
            )
            if exit_plan_called:
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration)
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self._run_hooks("turn_end", ctx)
                for he in self._drain_hook_events():
                    yield he
            yield TurnComplete(turn=iteration)


    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if not self.team_name or not self._team_manager:
            return
        try:
            mailbox = self._team_manager.get_mailbox(self.team_name)
            if mailbox is None:
                return
            messages = mailbox.consume(self.agent_id)
            for msg in messages:
                prefix = f"[Message from {msg.from_agent}]"
                if msg.message_type != "text":
                    prefix = f"[{msg.message_type} from {msg.from_agent}]"
                content = f"{prefix} {msg.content}"
                conversation.add_user_message(content)
        except Exception as e:
            log.debug("Mailbox consumption failed: %s", e)

    def _build_permission_description(self, tc: ToolCallComplete) -> str:
        """为 HITL 权限确认生成人类可读的操作描述。"""
        return PermissionChecker.describe_tool_action(tc.tool_name, tc.arguments)

    async def _execute_single_tool_direct(
        self, tc: ToolCallComplete,
        approval: Callable[[ToolCallComplete], Awaitable[PermissionResponse]] | None = None,
        operation_id: str | None = None,
    ) -> _ToolExecResult:
        start = time.monotonic()
        context = ToolRuntimeContext(
            cwd=Path(self.work_dir).resolve(), agent_id=self.agent_id,
            sandbox_root=Path(self.sandbox_root) if self.sandbox_root else None,
            spawn_allowed=self.spawn_allowed, file_versions=self._file_versions,
            expected_versions=self._turn_expected_versions if self._turn_expected_versions is not None else dict(self._file_versions),
            file_history=self.file_history,
        )
        ledger = self.recovery or recovery_runtime()
        if ledger and operation_id is None:
            operation_id = ledger.begin_operation("tool", tc.tool_name, tc.arguments,
                cwd=self.work_dir, tool_call_id=tc.tool_id, state="planned")
        with ledger.activate() if ledger else nullcontext():
            with ledger.operation_context(operation_id) if ledger else nullcontext():
                with bind_runtime(context):
                    try:
                        result = await self._execute_tool_core(tc, approval, operation_id)
                    except BaseException:
                        if ledger and not ledger.store.failed:
                            state = ledger.store.rows("SELECT state FROM operations WHERE operation_id=?", (operation_id,))[0]["state"]
                            if state in {"planned", "waiting_approval"}:
                                ledger.finish_not_started(operation_id, "Interrupted before dispatch; no authorization was reused")
                        raise
            if ledger:
                state = ledger.store.rows("SELECT state FROM operations WHERE operation_id=?", (operation_id,))[0]["state"]
                if state in {"planned", "waiting_approval"}:
                    ledger.finish_not_started(operation_id, result.output, is_error=result.is_error)
        return _ToolExecResult(tc.tool_id, tc.tool_name, result, time.monotonic() - start,
                               self.registry.get(tc.tool_name) is None)

    async def _execute_tool_core(
        self, tc: ToolCallComplete,
        approval: Callable[[ToolCallComplete], Awaitable[PermissionResponse]] | None,
        operation_id: str | None = None,
    ) -> ToolResult:
        ledger = self.recovery or recovery_runtime()
        if ledger:
            ledger.ensure_ready()
        if self.execution_guard and (reason := self.execution_guard()):
            return ToolResult(reason, True)
        tool = self.registry.get(tc.tool_name)
        if tool is None:
            return ToolResult(f"Error: unknown tool '{tc.tool_name}'", True)
        if not self.registry.is_enabled(tc.tool_name):
            return ToolResult(f"Error: tool '{tc.tool_name}' is disabled", True)
        if tc.tool_name in {"EnterWorktree", "ExitWorktree"} and self._pending_tool_turn and len(self._pending_tool_turn.collector.response.tool_calls) != 1:
            return ToolResult("Switch worktrees in a separate tool turn; this batch keeps its original working directory.", True)
        if tc.arguments_error:
            return ToolResult(f"Parameter JSON error: {tc.arguments_error}\nRaw arguments: {tc.raw_arguments}", True)
        try:
            params = tool.validate_arguments(tc.arguments)
            arguments = normalize_local_arguments(tc.tool_name, params.model_dump(exclude_unset=False))
            params = tool.validate_arguments(arguments)
        except Exception as e:
            return ToolResult(f"Parameter validation error: {e}", True)
        tc = replace(tc, arguments=arguments)
        file_path = self._infer_file_path(arguments)
        if self.hook_engine:
            ctx = self._build_hook_context("pre_tool_use", tool_name=tc.tool_name,
                                           tool_args=arguments, file_path=file_path)
            rejection = await self.hook_engine.run_pre_tool_hooks(ctx)
            if rejection is not None:
                return ToolResult(f"Hook rejected [{rejection.hook_id}]: {rejection.reason}", True)
        if self.execution_guard and (reason := self.execution_guard()):
            return ToolResult(reason, True)
        if not self.registry.is_enabled(tc.tool_name) or self.registry.get(tc.tool_name) is not tool:
            return ToolResult("Tool scope changed before execution", True)
        execution_cwd, execution_sandbox = self.work_dir, self.sandbox_root
        execution_mode = self.permission_mode
        approved_once = False
        if self.permission_checker:
            controller = self.approval_controller
            smart = bool(controller and approval and self.parent_id is None
                         and controller.active(self.permission_checker.mode) and tc.tool_name == "Bash")
            decision = (self.permission_checker.check(tool, arguments, smart=True) if smart
                        else self.permission_checker.check(tool, arguments))
            if smart and not decision.review_eligible:
                log.info("permission tool=Bash effect=%s source=%s", decision.effect, decision.source)
                controller.notify(f"规则判定 · {decision.reason}")
            if decision.effect == "deny":
                return ToolResult(f"Permission denied: {decision.reason}", True)
            if decision.effect == "ask":
                if ledger:
                    ledger.wait_for_approval(operation_id)
                if approval is None:
                    return ToolResult("Permission denied: non-interactive agent cannot prompt user; authorize this operation in the main session", True)
                request = None
                reviewed = smart and decision.review_eligible
                result = None
                if smart:
                    runtime = current_runtime()
                    if runtime and str(runtime.cwd) != self.work_dir:
                        return ToolResult("Permission invalidated: working directory changed; retry the command.", True)
                    request = build_request(self, tool, arguments, decision.source)
                if reviewed:
                    result = await controller.review(request)
                response = PermissionResponse.ALLOW if result and result.allowed else None
                if response is None:
                    allow_edits = (self.parent_id is None
                                   and self.permission_mode == PermissionMode.DEFAULT
                                   and tc.tool_name in {"WriteFile", "EditFile"}
                                   and tool.category == "write"
                                   and decision.source == "mode_fallback")
                    reason = result.reason if result else decision.reason
                    if tc.tool_name in {"WriteFile", "EditFile"} and not Path(file_path).is_relative_to(Path(self.work_dir)):
                        reason += "；项目外写入不在本工作区检查点保护范围内"
                    prompt = PermissionCall(tc.tool_id, tc.tool_name, arguments,
                                            approval_reason=reason,
                                            approval_cwd=self.work_dir,
                                            allow_always=not reviewed and getattr(tool, "allow_always", True),
                                            allow_edits=allow_edits)
                    response = await approval(prompt)
                    if response == PermissionResponse.ALLOW_EDITS:
                        current = self.permission_checker.check(tool, arguments)
                        runtime = current_runtime()
                        if (not allow_edits or self.permission_mode != PermissionMode.DEFAULT
                                or current.effect != "ask" or current.source != "mode_fallback"
                                or (runtime and str(runtime.cwd) != self.work_dir)
                                or self.registry.get(tc.tool_name) is not tool):
                            return ToolResult("Permission invalidated: enabling acceptEdits is not available for this request; retry the operation.", True)
                        self.set_permission_mode(PermissionMode.ACCEPT_EDITS)
                        execution_mode = self.permission_mode
                        response = PermissionResponse.ALLOW
                    if response not in (PermissionResponse.ALLOW, PermissionResponse.ALLOW_ALWAYS):
                        if smart:
                            controller.record_denial(arguments["command"])
                        return ToolResult("Permission denied: 用户拒绝了此操作", True)
                    if reviewed and response == PermissionResponse.ALLOW_ALWAYS:
                        return ToolResult("Permission denied: automatic review permits single-use approval only", True)
                approved_once = True
                if request is not None:
                    # No await between this check and execute's preparation of
                    # the actual command. Changes require a fresh tool request.
                    current = build_request(self, tool, params.model_dump(), decision.source)
                    if (current.binding != request.binding or arguments != params.model_dump()
                            or self.registry.get(tc.tool_name) is not tool):
                        return ToolResult("Permission invalidated: command, authorization or execution conditions changed; retry the command.", True)
                if response == PermissionResponse.ALLOW_ALWAYS and not reviewed:
                    if not getattr(tool, "allow_always", True):
                        return ToolResult("Permission denied: this tool requires single-use approval", True)
                    from nanocursor.permissions.rules import Rule, extract_content
                    content = extract_content(tc.tool_name, arguments)
                    self.permission_checker.rule_engine.append_local_rule(Rule(tc.tool_name, content, "allow"))
                    self.permission_checker.add_session_allow(tc.tool_name, content)
        if (self.work_dir != execution_cwd or self.sandbox_root != execution_sandbox
                or self.permission_mode != execution_mode or not self.registry.is_enabled(tc.tool_name)
                or self.registry.get(tc.tool_name) is not tool):
            return ToolResult("Permission invalidated: execution scope changed while awaiting approval; request a new operation.", True)
        if self.permission_checker:
            final_decision = self.permission_checker.check(tool, arguments)
            if final_decision.effect == "deny" or (final_decision.effect == "ask" and not approved_once):
                return ToolResult(f"Permission invalidated: {final_decision.reason}", True)
        result = ToolResult("Tool execution cancelled; side effects may already have occurred.", True)
        old_session = None
        if tc.tool_name in ("EnterWorktree", "ExitWorktree"):
            if not self.spawn_allowed:
                return ToolResult("Sub-agents cannot change the parent worktree session.", True)
            old_session = tool._manager.get_current_session()
        if ledger:
            ledger.store.put_metadata("dispatch", operation_id, {"arguments": arguments, "cwd": self.work_dir})
            ledger.start_operation(operation_id)
        if self.file_history is not None and tc.tool_name not in {"WriteFile", "EditFile"} and not tool.is_read_only:
            self.file_history.note_uncovered(f"{tc.tool_name}: changes outside built-in file checkpoints",
                                             entry_id=operation_id)
        try:
            with ledger.operation_context(operation_id) if ledger else nullcontext():
                result = await tool.execute(params)
            if ledger:
                if result.outcome_unknown:
                    ledger.mark_unknown(operation_id, result.output)
                else:
                    ledger.finish_operation(operation_id, result.output, is_error=result.is_error)
            if not result.is_error and tc.tool_name == "EnterWorktree":
                session = tool._manager.get_current_session()
                self.set_work_dir(session.worktree_path, isolated=True)
            elif not result.is_error and tc.tool_name == "ExitWorktree" and old_session:
                self.set_work_dir(old_session.original_cwd)
        except RecoveryError as exc:
            if ledger and not ledger.store.failed:
                ledger.mark_unknown(operation_id, f"Execution stopped by recovery protection: {exc}")
            raise
        except BaseException as exc:
            if ledger:
                ledger.mark_unknown(operation_id, f"Execution interrupted: {type(exc).__name__}: {exc}")
            if not isinstance(exc, Exception):
                raise
            result = ToolResult(f"Tool execution error: {exc}; outcome requires recovery review.", True, outcome_unknown=True)
        if self.hook_engine and not result.outcome_unknown:
            ctx = self._build_hook_context("post_tool_use", tool_name=tc.tool_name,
                                           tool_args=arguments, file_path=file_path,
                                           error=result.output if result.is_error else "")
            await self._run_hooks("post_tool_use", ctx)
        self._snapshot_for_recovery(tc, result)
        return result

    async def _execute_batch_parallel(self, calls: list[ToolCallComplete]) -> list[_ToolExecResult]:
        executor = StreamingExecutor()
        for tc in calls:
            tool = self.registry.get(tc.tool_name)
            executor.submit(tc, lambda tc=tc: self._execute_single_tool_direct(tc),
                            concurrent=bool(tool and tool.is_concurrency_safe and tool.is_read_only))
        try:
            return [item async for item in executor.iter_results() if isinstance(item, _ToolExecResult)]
        finally:
            await executor.cancel_and_wait()

    async def _execute_tool(self, tc: ToolCallComplete) -> AsyncIterator[Any]:
        executor = StreamingExecutor()
        executor.submit(tc, lambda: self._execute_single_tool_direct(tc, executor.request_permission))
        try:
            async for item in executor.iter_results():
                if isinstance(item, PermissionRequest):
                    yield item
                else:
                    yield item.result, item.elapsed, item.is_unknown
        finally:
            await executor.cancel_and_wait()

    def _snapshot_for_recovery(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """捕获 ReadFile 刚交给模型的内容，以便 Layer 2 压缩对话后
        auto_compact 能重新附加这些数据。每次 ReadFile 多一次磁盘读取，
        比从 tool 输出中反向解析行号要划算。
        """
        if result.is_error or tc.tool_name != "ReadFile":
            return
        path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return
        self.recovery_state.record_file_read(path, content)

    async def _extract_memories(
        self, conversation: ConversationManager
    ) -> None:
        """触发记忆提取，对齐 Go 版 inProgress + pendingContext 合并策略。

        当提取正在进行时，新的触发不会启动并发提取，而是标记 _pending_extraction。
        当前提取完成后检查该标志，如果有 pending 则立即执行一次尾随提取，
        防止多个触发器同时执行导致重复提取。
        """
        if not self.memory_manager:
            return

        # 合并策略：正在提取时暂存新请求，等当前提取完成后尾随执行
        if self._extracting:
            log.debug("[extractMemories] extraction in progress — stashing for trailing run")
            self._pending_extraction = True
            return

        self._extracting = True
        try:
            if self.recovery:
                self.recovery.ensure_ready()
            await self.memory_manager.extract(self.client, conversation, self.protocol)
        except RecoveryError:
            raise
        except Exception as e:
            log.debug("Memory extraction failed: %s", e)
        finally:
            self._extracting = False
            # 检查是否有尾随提取请求
            if self._pending_extraction:
                self._pending_extraction = False
                log.debug("[extractMemories] running trailing extraction for stashed context")
                # 递归调用自身处理尾随请求
                await self._extract_memories(conversation)

    async def manual_compact(
        self, conversation: ConversationManager
    ) -> CompactNotification | ErrorEvent:
        # auto_compact 会用摘要替换 conversation.history，所有 tool-result 内容
        # （原始或已替换的）都将被丢弃。这里跳过 apply_tool_result_budget —
        # 它在主循环中的唯一目的是为 LLM 调用生成 api_conv，而本路径不需要
        # 发起看到替换结果的 LLM 调用（auto_compact 内部的摘要调用操作的是原始对话）。
        result = await auto_compact(
            conversation,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=self.recovery_state,
            tool_schemas=self.registry.get_all_schemas(self.protocol),
            transcript_path=self._transcript_path,
        )
        if isinstance(result, CompactEvent):
            if self.inject_environment_context:
                env_context = build_environment_context(
                    self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
                )
                conversation.inject_environment(env_context)
            conversation.inject_long_term_memory(self.instructions_content, "")
            if self.memory_manager:
                from nanocursor.memory.context import sync_memory
                recall = await self.memory_recall.prepare("", self.memory_manager.user_mem_dir,
                                                         self.memory_manager.project_mem_dir)
                sync_memory(conversation, recall, self.memory_recall, self.context_window,
                            getattr(self.client, "max_output_tokens", 0))
            return CompactNotification(
                before_tokens=result.before_tokens,
                message=f"上下文已压缩（压缩前 {result.before_tokens:,} tokens）",
                boundary=result.boundary,
            )
        return ErrorEvent(message=result or "压缩失败：对话历史为空或未达到压缩条件")

    async def run_to_completion(
        self, task: str, conversation: ConversationManager | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        conversation = conversation if conversation is not None else ConversationManager()
        if task:
            conversation.add_user_message(task)
        response_text = ""
        last_text = ""
        async with aclosing(self.run(conversation, interactive=False)) as events:
            async for event in events:
                if isinstance(event, StreamText):
                    response_text += event.text
                    last_text = response_text
                    if event_callback:
                        event_callback({"type": "stream_text", "text": event.text})
                elif isinstance(event, TurnComplete):
                    response_text = ""
                elif isinstance(event, ToolUseEvent) and event_callback:
                    event_callback({"type": "tool_use", "toolName": event.tool_name, "args": event.arguments})
                elif isinstance(event, UsageEvent) and event_callback:
                    event_callback({"type": "usage", "usage": {
                        "inputTokens": event.input_tokens, "outputTokens": event.output_tokens}})
                elif isinstance(event, ErrorEvent) and event.fatal:
                    raise AgentRunError(event.message)
        return last_text

    async def _execute_tool_noninteractive(self, tc: ToolCallComplete) -> ToolResult:
        return (await self._execute_single_tool_direct(tc)).result

    def _maybe_persist_or_truncate(self, tool_use_id: str, text: str) -> str:
        from nanocursor.context.manager import (
            SINGLE_RESULT_CHAR_LIMIT,
            make_persisted_preview,
            persist_tool_result,
        )

        if len(text) > SINGLE_RESULT_CHAR_LIMIT:
            fp = persist_tool_result(tool_use_id, text, self.session_dir)
            return make_persisted_preview(text, fp)
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"
        return text
