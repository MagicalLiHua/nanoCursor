"""Runtime streaming adapter for the legacy Agent loop.

This service keeps token streaming and agent-pool status events out of the
legacy runtime while still receiving the actual engine function as a dependency.
That preserves existing monkeypatch-based tests during the incremental split.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from src.api.models import AgentEvent
from src.api.services.runtime_turn_service import context_pack_to_text


AgentLoopStream = Callable[..., AsyncIterator[tuple[Any, ...]]]
ToolCheckCallback = Callable[[str, dict[str, Any]], Awaitable[Any]]
ToolCallCallback = Callable[[str, dict[str, Any], str], None]
CancelCheck = Callable[[], bool]
MetricsCallback = Callable[[int, int], None]
EmitEvent = Callable[..., Any]


@dataclass(slots=True)
class RuntimeStreamResult:
    text: str
    token_counter: int


def build_turn_system(
    *,
    base_system: str,
    turn_context: dict[str, Any] | None = None,
    uses_runtime_turn_loop: bool = False,
    intent_route: str = "",
    has_tools: bool = False,
) -> str:
    """Build the system prompt for one streamed runtime turn."""
    turn_system = base_system
    if turn_context:
        turn_system = f"{turn_system}\n\n{context_pack_to_text(turn_context)}"
    if uses_runtime_turn_loop:
        turn_system = (
            f"{turn_system}\n\n"
            "本轮由 Runtime Step Controller 管理。只执行当前用户请求需要的动作；"
            "不要创建实现任务，不要调用未提供的工具。"
        )
    if has_tools:
        turn_system = (
            f"{turn_system}\n\n"
            "工具调用必须使用系统提供的原生 tool_use/tool_call 机制；"
            "不要在正文中输出 <execute>、<cmd>、JSON 工具片段或 shell 命令伪协议。"
            "如果需要读写文件或执行命令，必须调用对应工具。"
        )
    if intent_route == "small_edit":
        turn_system = (
            f"{turn_system}\n"
            "这是受控 small_edit：必须实际完成局部文件修改；修改后检查 Diff，"
            "必要时运行最小范围测试。不要删除文件、安装依赖、执行任意 shell 或创建子 Agent。"
        )
    return turn_system


def make_agent_pool_status_callback(
    *,
    thread_id: str,
    workspace_dir: str,
    emit_event: EmitEvent,
) -> Callable[[Any, str], None]:
    """Create a callback that translates agent pool status into AgentHub events."""

    def _agent_pool_status_callback(handle: Any, event: str) -> None:
        emit_event(
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

    return _agent_pool_status_callback


async def stream_model_response(
    *,
    thread_id: str,
    workspace_dir: str,
    messages: list[dict[str, Any]],
    base_system: str,
    tools: list[dict[str, Any]],
    agent_loop_stream: AgentLoopStream,
    token_broker: Any,
    token_counter: int = 0,
    turn_context: dict[str, Any] | None = None,
    uses_runtime_turn_loop: bool = False,
    intent_route: str = "",
    on_tool_check: ToolCheckCallback | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_cancel_check: CancelCheck | None = None,
    on_llm_response: MetricsCallback | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> RuntimeStreamResult:
    """Stream one Agent loop response and publish transient token events."""
    response_text = ""
    turn_system = build_turn_system(
        base_system=base_system,
        turn_context=turn_context,
        uses_runtime_turn_loop=uses_runtime_turn_loop,
        intent_route=intent_route,
        has_tools=bool(tools),
    )
    async for event_type, *event_data in agent_loop_stream(
        messages=messages,
        system=turn_system,
        tools=tools,
        max_turns=100,
        on_tool_check=on_tool_check,
        on_tool_call=on_tool_call,
        on_cancel_check=on_cancel_check,
        session_id=thread_id,
        runtime_context=runtime_context or {},
    ):
        if event_type == "token":
            text = str(event_data[0])
            response_text += text
            token_counter += 1
            token_broker.publish(
                thread_id,
                AgentEvent(
                    id=f"{thread_id}-tok-{token_counter}",
                    thread_id=thread_id,
                    type="token",
                    timestamp=time.time(),
                    agent="lead",
                    content=text,
                    payload={"delta": text},
                ),
            )
        elif event_type == "metrics":
            if on_llm_response:
                on_llm_response(int(event_data[0]), int(event_data[1]))
        elif event_type == "error":
            raise RuntimeError(event_data[0])
        # tool_start, tool_input, and tool_result are handled by callbacks.
    return RuntimeStreamResult(text=response_text, token_counter=token_counter)
