from __future__ import annotations

from nanocursor.commands.registry import Command, CommandContext, CommandType
from nanocursor.conversation import ConversationManager


async def handle_clear(ctx: CommandContext) -> None:
    tasks = getattr(ctx.ui, "task_manager", None)
    if tasks and tasks.has_active_tasks():
        ctx.ui.add_system_message("Wait for background tasks to finish before clearing the session.")
        return
    prepare = ctx.config.get("prepare_session_change")
    if prepare:
        await prepare()
    if ctx.session_manager:
        new_session = ctx.session_manager.create()
        if ctx.session:
            ctx.session.close()
        ctx.config["set_session"](new_session)

    ctx.config["set_conversation"](ConversationManager())

    if ctx.agent:
        ctx.agent._loop_count = 0
        ctx.agent.clear_active_skills()
        # 重置 token 计数
        ctx.agent.total_input_tokens = 0
        ctx.agent.total_output_tokens = 0
        ctx.agent.usage_missing_requests = 0

    ctx.config["clear_chat"]()
    ctx.ui.refresh_status()
    ctx.ui.add_system_message("对话已清除，新会话已创建")


CLEAR_COMMAND = Command(
    name="clear",
    description="清除对话历史",
    usage="/clear",
    type=CommandType.LOCAL_UI,
    handler=handle_clear,
)
