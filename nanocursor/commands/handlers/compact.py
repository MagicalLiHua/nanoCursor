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

    from nanocursor.agent import CompactNotification, ErrorEvent


    from copy import deepcopy
    original = deepcopy(ctx.conversation)
    ctx.ui.add_system_message("正在压缩当前上下文…")
    result = await ctx.agent.manual_compact(ctx.conversation)
    if isinstance(result, CompactNotification):
        # 持久化 compact_boundary，使后续 resume 可重建压缩后的状态。
        # manual_compact 已重写了 ctx.conversation；下一次 _send_message
        # 会重新捕获 history_cursor，所以这里无需手动重置。
        if ctx.session is not None and result.boundary is not None:
            from nanocursor.memory.session import SessionMetadataError, make_compact_boundary

            try:
                ctx.session.append_record(make_compact_boundary(result.boundary.summary, result.boundary.keep,
                                                               messages=ctx.conversation.history))
            except SessionMetadataError as exc:
                ctx.ui.add_system_message(f"压缩结果已保存，但会话元数据更新失败: {exc}")
            except Exception:
                ctx.conversation.__dict__.clear()
                ctx.conversation.__dict__.update(original.__dict__)
                raise
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
