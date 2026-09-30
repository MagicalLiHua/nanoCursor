from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from nanocursor.tools.base import ToolCallComplete

from nanocursor.conversation import ConversationManager, Message


@dataclass
class CompactBoundary:
    """Layer 2 压缩的结构化结果，上交给 session 层处理。

    `summary` 是大模型对被摘要前缀生成的摘要；`keep` 是 auto_compact 原样保留、
    未做改动的近期尾部消息。session 层（持有 sessionId / 文件句柄）会把二者一起
    内联进一条 compact_boundary 记录，这样 resume 时就能重建压缩后的状态。
    用这种方式把写操作解耦出去，能让 auto_compact 保持纯粹、不依赖任何 session。
    """

    summary: str
    keep: list[Message]


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
    fatal: bool = True
    code: str = "agent_error"


class AgentRunError(RuntimeError):
    """A terminal loop failure, also propagated to background callers."""


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: "CompactBoundary | None" = None
    # The persistence owner can restore the exact history and usage anchor if
    # publishing this candidate boundary fails. No provider/tool is replayed.
    prior_conversation: ConversationManager | None = None


@dataclass
class MemoryContextChanged:
    prior_conversation: ConversationManager


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
    ALLOW_EDITS = "allow_edits"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]
    reason: str = ""
    cwd: str = ""
    allow_always: bool = True
    allow_edits: bool = False


@dataclass
class PermissionCall(ToolCallComplete):
    """UI-only metadata; never included in tool arguments sent to the model."""
    approval_reason: str = ""
    approval_cwd: str = ""
    allow_always: bool = True
    allow_edits: bool = False


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
    | MemoryContextChanged
    | HookEvent
)
