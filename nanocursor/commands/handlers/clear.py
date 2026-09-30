from __future__ import annotations

from nanocursor.commands.ports import command_services
from nanocursor.commands.registry import Command, CommandContext, CommandType
from nanocursor.conversation import ConversationManager


async def handle_clear(ctx: CommandContext) -> None:
    sessions = command_services(ctx).sessions
    if sessions.has_active_tasks():
        ctx.ui.add_system_message("Wait for background tasks to finish before clearing the session.")
        return
    prepare = command_services(ctx).sessions.prepare_session_change
    if prepare:
        await prepare()
    if ctx.session_manager:
        new_session = ctx.session_manager.create()
        if ctx.session:
            ctx.session.close()
        command_services(ctx).sessions.set_session(new_session)

    command_services(ctx).sessions.set_conversation(ConversationManager())

    if ctx.agent:
        ctx.agent.reset_usage()

    command_services(ctx).sessions.clear_chat()
    ctx.ui.refresh_status()
    ctx.ui.add_system_message("对话已清除，新会话已创建")


CLEAR_COMMAND = Command(
    name="clear",
    description="清除对话历史",
    usage="/clear",
    type=CommandType.LOCAL_UI,
    handler=handle_clear,
)
