from __future__ import annotations

import asyncio
from contextlib import aclosing
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from nanocursor.client import LLMClient
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
from nanocursor.hooks import HookContext, HookEngine
from nanocursor.hooks.engine import HookNotification
from nanocursor.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from nanocursor.tools import ToolRegistry
from nanocursor.tools.runtime import ToolRuntimeContext, bind_runtime, normalize_local_arguments
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
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------

@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: "CompactBoundary | None" = None


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


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


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
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
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


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
                call.tool_name, PermissionChecker.describe_tool_action(call.tool_name, call.arguments), future))
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
        return [self._result(call, task) for call, task in self._tasks]


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
    ) -> None:
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
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        self.context_window = context_window
        self.session_dir = ensure_session_dir(work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        self.replacement_state: ContentReplacementState = create_replacement_state()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self.system_prompt_override = system_prompt_override
        self.inject_environment_context = inject_environment_context
        self._loop_count = 0
        # 记忆提取合并策略（对齐 Go 版 inProgress + pendingContext）：
        # _extracting: 标记是否有提取正在进行
        # _pending_extraction: 提取期间又触发了新请求，标记需要尾随提取
        self._extracting = False
        self._pending_extraction = False
        self._consolidator: MemoryConsolidator | None = None
        if memory_manager is not None:
            from nanocursor.memory.consolidation import MemoryConsolidator
            self._consolidator = MemoryConsolidator(work_dir)
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

        # 非阻塞 memory recall：prefetch task 与主 LLM 调用并行，工具执行后注入
        self.memory_recall_task: Any | None = None
        self._memory_recall_consumed: bool = False

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(Path(self.work_dir) / ".nanocursor" / "sessions" / f"{self.session_id}.jsonl")
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
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

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

    def set_work_dir(self, work_dir: str, *, isolated: bool = False) -> None:
        from nanocursor.permissions import PathSandbox
        self.work_dir = str(Path(work_dir).resolve())
        self.sandbox_root = self.work_dir if isolated else None
        self._file_versions.clear()
        if self.permission_checker:
            self.permission_checker.sandbox = PathSandbox(self.work_dir)

    async def run(self, conversation: ConversationManager, *, interactive: bool = True) -> AsyncIterator[AgentEvent]:
        try:
            async for event in self._run(conversation, interactive=interactive):
                yield event
        finally:
            # Also runs for provider failures and generator close, not only UI cancel.
            cleanup = asyncio.create_task(self._close_pending_tool_turn(conversation))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            self._turn_expected_versions = None

    async def _close_pending_tool_turn(self, conversation: ConversationManager) -> None:
        pending = self._pending_tool_turn
        if pending is None:
            return
        results = await pending.executor.cancel_and_wait()
        response = pending.collector.response
        if response.tool_calls:
            if not pending.committed:
                conversation.add_assistant_message(response.text, [
                    ToolUseBlock(tc.tool_id, tc.tool_name, tc.arguments) for tc in response.tool_calls
                ], thinking_blocks=[ConvThinkingBlock(tb.thinking, tb.signature) for tb in response.thinking_blocks])
            conversation.add_tool_results_message([
                ToolResultBlock(r.tool_id, self._maybe_persist_or_truncate(r.tool_id, r.result.output), r.result.is_error)
                for r in results
            ])
        self._pending_tool_turn = None

    async def _run(self, conversation: ConversationManager, *, interactive: bool) -> AsyncIterator[AgentEvent]:
        self._current_conversation = conversation
        env_context = ""
        if self.inject_environment_context:
            env_context = build_environment_context(
                self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
            )
            conversation.inject_environment(env_context)

        memory_content = self.memory_manager.load() if self.memory_manager else ""
        conversation.inject_long_term_memory(self.instructions_content, memory_content)

        if self.hook_engine:
            ctx = self._build_hook_context("session_start")
            await self.hook_engine.run_hooks("session_start", ctx)
            for he in self._drain_hook_events():
                yield he

        iteration = 0
        consecutive_unknown = 0
        parameter_error_turns = 0
        max_tokens_escalated = False
        output_recoveries = 0

        while True:
            iteration += 1

            if self.max_iterations > 0 and iteration > self.max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self.max_iterations})"
                )
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._consume_mailbox(conversation)
            if self.notification_fn:
                for note in self.notification_fn():
                    conversation.add_system_reminder(note)

            if self.hook_engine:
                ctx = self._build_hook_context("pre_send")
                await self.hook_engine.run_hooks("pre_send", ctx)
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

            if self.plan_mode:
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
            if isinstance(compact_result, CompactEvent):
                yield CompactNotification(
                    before_tokens=compact_result.before_tokens,
                    message=f"上下文已压缩（压缩前 {compact_result.before_tokens:,} tokens）",
                    boundary=compact_result.boundary,
                )
                if env_context:
                    conversation.inject_environment(env_context)
                mem = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(
                    self.instructions_content, mem
                )
                # 压缩后重新应用 budget（就地修改）
                apply_tool_result_budget(
                    conversation, self.session_dir, self.replacement_state
                )
            elif isinstance(compact_result, str):
                yield ErrorEvent(message=compact_result)

            collector = StreamCollector()
            executor = StreamingExecutor()
            self._pending_tool_turn = PendingToolTurn(collector, executor)
            self._turn_expected_versions = dict(self._file_versions)
            llm_stream = self.client.stream(conversation, system=system, tools=tools)
            async for event in collector.consume(llm_stream):
                if isinstance(event, ToolUseEvent):
                    tc = collector.response.tool_calls[-1]
                    tool = self.registry.get(tc.tool_name)
                    approval = executor.request_permission if interactive else None
                    executor.submit(tc, lambda tc=tc: self._execute_single_tool_direct(tc, approval),
                                    concurrent=bool(tool and tool.is_concurrency_safe and tool.is_read_only))
                yield event

            response = collector.response

            if self.hook_engine:
                ctx = self._build_hook_context("post_receive", message=response.text)
                await self.hook_engine.run_hooks("post_receive", ctx)
                for he in self._drain_hook_events():
                    yield he

            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            yield UsageEvent(
                input_tokens=self.total_input_tokens,
                output_tokens=self.total_output_tokens,
            )

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens" and not response.tool_calls:
                if not max_tokens_escalated:
                    self.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                    max_tokens_escalated = True
                    if response.text:
                        conversation.add_assistant_message(
                            response.text, thinking_blocks=conv_thinking
                        )
                        conversation.add_user_message(
                            "Output token limit hit. Resume directly from where you stopped. "
                            "Do not apologize or repeat previous content. Pick up mid-thought if needed."
                        )
                    yield RetryEvent(reason="max_tokens escalation")
                    continue
                elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                    output_recoveries += 1
                    conversation.add_assistant_message(
                        response.text, thinking_blocks=conv_thinking
                    )
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces."
                    )
                    yield RetryEvent(
                        reason=f"max_tokens recovery {output_recoveries}/{MAX_OUTPUT_TOKENS_RECOVERIES}"
                    )
                    continue
            else:
                output_recoveries = 0

            if not response.tool_calls:
                self._pending_tool_turn = None
                conversation.add_assistant_message(
                    response.text, thinking_blocks=conv_thinking
                )
                self._loop_count += 1
                if (
                    self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0
                    and self.memory_manager
                ):
                    asyncio.ensure_future(self._extract_memories(conversation))
                if self._consolidator is not None:
                    asyncio.ensure_future(
                        self._consolidator.maybe_run(self.client, conversation, self.protocol)
                    )
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self.hook_engine.run_hooks("turn_end", ctx)
                    ctx = self._build_hook_context("session_end")
                    await self.hook_engine.run_hooks("session_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                if self.file_history is not None:
                    summary = response.text[:60] + "..." if len(response.text) > 60 else response.text
                    self.file_history.make_snapshot(len(conversation.history), summary)
                yield LoopComplete(total_turns=iteration)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in response.tool_calls
            ]
            conversation.add_assistant_message(
                response.text, tool_uses, thinking_blocks=conv_thinking
            )
            self._pending_tool_turn.committed = True
            # 在 assistant 回复加入历史后锚定实际用量：基线（input + cache + output）
            # 覆盖到当前位置，因此下一轮迭代顶部的 auto-compact 检查只需对
            # 接下来追加的 tool results 做字符估算。
            conversation.record_usage_anchor(
                response.input_tokens,
                response.output_tokens,
                response.cache_read,
                response.cache_creation,
            )

            # 收集流式执行器中已提交的工具结果（工具在 LLM 流式输出期间已开始执行）
            tool_results: list[ToolResultBlock] = []
            async for item in executor.iter_results():
                if isinstance(item, PermissionRequest):
                    yield item
                    continue
                br = item
                consecutive_unknown = consecutive_unknown + 1 if br.is_unknown else 0
                content = self._maybe_persist_or_truncate(br.tool_id, br.result.output)
                tool_results.append(ToolResultBlock(br.tool_id, content, br.result.is_error))
                yield ToolResultEvent(br.tool_id, br.tool_name, br.result.output, br.result.is_error, br.elapsed)

            conversation.add_tool_results_message(tool_results)
            self._pending_tool_turn = None
            parameter_error_turns = parameter_error_turns + 1 if any(
                r.is_error and r.content.startswith(("Parameter JSON error:", "Parameter validation error:"))
                for r in tool_results
            ) else 0
            if parameter_error_turns >= 3:
                yield ErrorEvent(message="Agent stopped after three consecutive turns with invalid tool arguments")
                break
            if consecutive_unknown >= 3:
                yield ErrorEvent(
                    message="Agent terminated: too many consecutive unknown tool calls"
                )
                break

            exit_plan_called = any(
                tc.tool_name == "ExitPlanMode" for tc in response.tool_calls
            )
            # 非阻塞 memory recall：工具执行完后检查 prefetch 是否就绪
            if self.memory_recall_task and not self._memory_recall_consumed:
                if self.memory_recall_task.done():
                    try:
                        recall = self.memory_recall_task.result()
                        if recall:
                            conversation.add_system_reminder(recall)
                    except Exception:
                        pass
                    self._memory_recall_consumed = True

            if exit_plan_called:
                yield TurnComplete(turn=iteration)
                yield LoopComplete(total_turns=iteration)
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)
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
    ) -> _ToolExecResult:
        start = time.monotonic()
        context = ToolRuntimeContext(
            cwd=Path(self.work_dir).resolve(), agent_id=self.agent_id,
            sandbox_root=Path(self.sandbox_root) if self.sandbox_root else None,
            spawn_allowed=self.spawn_allowed, file_versions=self._file_versions,
            expected_versions=self._turn_expected_versions if self._turn_expected_versions is not None else dict(self._file_versions),
        )
        with bind_runtime(context):
            result = await self._execute_tool_core(tc, approval)
        return _ToolExecResult(tc.tool_id, tc.tool_name, result, time.monotonic() - start,
                               self.registry.get(tc.tool_name) is None)

    async def _execute_tool_core(
        self, tc: ToolCallComplete,
        approval: Callable[[ToolCallComplete], Awaitable[PermissionResponse]] | None,
    ) -> ToolResult:
        tool = self.registry.get(tc.tool_name)
        if tool is None:
            return ToolResult(f"Error: unknown tool '{tc.tool_name}'", True)
        if not self.registry.is_enabled(tc.tool_name):
            return ToolResult(f"Error: tool '{tc.tool_name}' is disabled", True)
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
        if self.permission_checker:
            decision = self.permission_checker.check(tool, arguments)
            if decision.effect == "deny":
                return ToolResult(f"Permission denied: {decision.reason}", True)
            if decision.effect == "ask":
                if approval is None:
                    return ToolResult("Permission denied: non-interactive agent cannot prompt user", True)
                response = await approval(tc)
                if response == PermissionResponse.DENY:
                    return ToolResult("Permission denied: 用户拒绝了此操作", True)
                if response == PermissionResponse.ALLOW_ALWAYS:
                    from nanocursor.permissions.rules import Rule, extract_content
                    content = extract_content(tc.tool_name, arguments)
                    self.permission_checker.rule_engine.append_local_rule(Rule(tc.tool_name, content, "allow"))
                    self.permission_checker.add_session_allow(tc.tool_name, content)
        result = ToolResult("Tool execution cancelled; side effects may already have occurred.", True)
        old_session = None
        if tc.tool_name in ("EnterWorktree", "ExitWorktree"):
            if not self.spawn_allowed:
                return ToolResult("Sub-agents cannot change the parent worktree session.", True)
            old_session = tool._manager.get_current_session()
        try:
            result = await tool.execute(params)
            if not result.is_error and tc.tool_name == "EnterWorktree":
                session = tool._manager.get_current_session()
                self.set_work_dir(session.worktree_path, isolated=True)
            elif not result.is_error and tc.tool_name == "ExitWorktree" and old_session:
                self.set_work_dir(old_session.original_cwd)
        except Exception as e:
            result = ToolResult(f"Tool execution error: {e}", True)
        finally:
            if self.hook_engine:
                ctx = self._build_hook_context("post_tool_use", tool_name=tc.tool_name,
                                               tool_args=arguments, file_path=file_path,
                                               error=result.output if result.is_error else "")
                await self.hook_engine.run_hooks("post_tool_use", ctx)
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
            await self.memory_manager.extract(
                self.client, conversation, self.protocol
            )
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
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(
                self.instructions_content, memory_content
            )
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
