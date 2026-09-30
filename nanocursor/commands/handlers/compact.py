from __future__ import annotations

from nanocursor.commands.registry import Command, CommandContext, CommandType


async def handle_compact(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return


    input_tokens = ctx.conversation.current_tokens()
    if input_tokens < 5000:
        ctx.ui.add_system_message(f"当前上下文约 {input_tokens:,}，无需压缩")
        return

    from nanocursor.events import CompactNotification, ErrorEvent


    from copy import deepcopy
    original = deepcopy(ctx.conversation)
    ctx.ui.add_system_message("正在压缩当前上下文…")
    result = await ctx.agent.manual_compact(ctx.conversation)
    if isinstance(result, CompactNotification):
        from dataclasses import replace
        from nanocursor.application.session import SessionPersistence

        SessionPersistence(ctx.session, ctx.conversation, ctx.ui.add_system_message).commit_compact(
            replace(result, prior_conversation=original), include_messages=True,
        )
        ctx.ui.add_system_message(f"上下文已压缩：约 {input_tokens:,} → {ctx.conversation.current_tokens():,} tokens。")
        ctx.ui.refresh_status()
    elif isinstance(result, ErrorEvent):
        ctx.ui.add_system_message(f"压缩失败: {result.message}")


COMPACT_COMMAND = Command(
    name="compact",
    aliases=["c"],
    description="压缩上下文",
    usage="/compact [保留重点]",
    type=CommandType.LOCAL,
    handler=handle_compact,
)
