from __future__ import annotations

import os
from types import SimpleNamespace

from rich.markup import escape

from nanocursor.commands.registry import Command, CommandContext, CommandType
from nanocursor.runtime import get_version
from nanocursor.status import collect_status, format_status_details


async def handle_status(ctx: CommandContext) -> None:
    # The TUI owns connection and provider state; small command adapters may
    # only supply an Agent and conversation through CommandContext.
    app = ctx.ui if hasattr(ctx.ui, "agent") else SimpleNamespace(
        agent=ctx.agent, conversation=ctx.conversation,
    )
    lines = [format_status_details(collect_status(app))]

    if ctx.session:
        m = ctx.session.meta
        lines.append(f"会话: {m.id}（{m.message_count} 条消息）")
    else:
        lines.append("会话: 无")

    if ctx.memory_manager:
        mem_entries = ctx.memory_manager.get_memories()
        lines.append(f"记忆: {len(mem_entries)} 条")

    work_dir = ctx.agent.work_dir if ctx.agent else os.getcwd()
    lines.append(f"工作目录: {work_dir}")
    lines.append(f"版本: v{get_version()}")

    ctx.ui.add_system_message(escape("\n".join(lines)))


STATUS_COMMAND = Command(
    name="status",
    aliases=["s"],
    description="显示状态信息",
    usage="/status",
    type=CommandType.LOCAL,
    handler=handle_status,
)
