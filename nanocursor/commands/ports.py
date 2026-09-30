from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from nanocursor.commands.registry import CommandContext, CommandRegistry
    from nanocursor.config import MemoryRecallConfig
    from nanocursor.conversation import ConversationManager, Message
    from nanocursor.memory.session import Session
    from nanocursor.skills.executor import SkillExecutor, SkillRunResult
    from nanocursor.skills.loader import SkillLoader
    from nanocursor.status import MCPServerStatus, StatusSnapshot


@dataclass
class SessionActions:
    set_session: Callable[[Session], None] | None = None
    set_conversation: Callable[[ConversationManager], None] | None = None
    clear_chat: Callable[[], None] | None = None
    render_restored: Callable[[list[Message]], Awaitable[None]] | None = None
    prepare_session_change: Callable[[], Awaitable[None]] | None = None
    has_active_tasks: Callable[[], bool] = lambda: False
    is_running: Callable[[], bool] = lambda: False
    get_resume_candidates: Callable[[], tuple[str, ...]] = lambda: ()
    set_resume_candidates: Callable[[tuple[str, ...]], None] = lambda candidates: None


@dataclass
class MemoryControls:
    recall_changed: Callable[[MemoryRecallConfig], None] = lambda config: None
    consolidation_status: Callable[[], str] | None = None
    set_consolidation: Callable[[bool], Awaitable[None]] | None = None


@dataclass
class CommandServices:
    sessions: SessionActions = field(default_factory=SessionActions)
    memory: MemoryControls = field(default_factory=MemoryControls)
    registry: CommandRegistry | None = None
    skill_loader: SkillLoader | None = None
    skill_executor: SkillExecutor | None = None
    register_owned_task: Callable[[asyncio.Task], object] | None = None
    queue_skill_result: Callable[[str, ConversationManager, str, SkillRunResult], None] | None = None
    session_id: str = ""
    is_session_current: Callable[[str], bool] = lambda session_id: True
    status_snapshot: Callable[[], StatusSnapshot] | None = None
    mcp_servers: Callable[[], list[MCPServerStatus]] | None = None
    mcp_connecting: Callable[[], bool] = lambda: False


def command_services(context: CommandContext) -> CommandServices:
    config = getattr(context, "config", {})
    if isinstance(config, CommandServices):
        return config
    from nanocursor.commands.compat import legacy_services
    return legacy_services(context)
