from __future__ import annotations

from nanocursor.commands.ports import command_services
from nanocursor.commands.registry import Command, CommandContext, CommandType


async def handle_memory(ctx: CommandContext) -> None:
    mm = ctx.memory_manager
    if mm is None:
        ctx.ui.add_system_message("记忆管理器未初始化")
        return


    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "recall":
        action = parts[1].strip() if len(parts) > 1 else "status"
        if ctx.agent is None:
            ctx.ui.add_system_message("记忆召回仅在主 Agent 初始化后可用")
            return
        service = ctx.agent.memory_recall
        if action in {"off", "local", "model"}:
            await service.cancel_and_wait()
            service.config.mode = action
            command_services(ctx).memory.recall_changed(service.config)
            if action == "off":
                await _remove_context(ctx, dynamic_only=True)
        elif action != "status":
            ctx.ui.add_system_message("用法: /memory recall [status | off | local | model]")
            return
        ctx.ui.add_system_message(service.status_text())
    elif sub == "consolidate":
        action = parts[1].strip() if len(parts) > 1 else "status"
        controls = command_services(ctx).memory
        if controls.consolidation_status is None:
            ctx.ui.add_system_message("后台记忆整理仅在主 Agent 交互界面中可用。")
            return
        if action in {"on", "off"}:
            await controls.set_consolidation(action == "on")
        elif action != "status":
            ctx.ui.add_system_message("用法: /memory consolidate [status | on | off]")
            return
        ctx.ui.add_system_message(controls.consolidation_status())
    elif sub == "":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "list":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "clear":
        prepare = command_services(ctx).sessions.prepare_session_change
        if prepare:
            await prepare()
        mm.clear()
        if ctx.agent:
            ctx.agent.memory_recall.invalidate()
        await _remove_context(ctx)
        ctx.ui.add_system_message("所有自动记忆已清空。")

    elif sub == "edit":
        ctx.ui.add_system_message(
            f"编辑记忆文件：\n"
            f"  用户级目录: {mm.user_mem_dir}\n"
            f"  项目级目录: {mm.project_mem_dir}"
        )

    else:
        ctx.ui.add_system_message(
            "用法: /memory [list | clear | edit | recall status|off|local|model | consolidate status|on|off]"
        )


async def _remove_context(ctx: CommandContext, *, dynamic_only: bool = False) -> None:
    if ctx.conversation is None or ctx.agent is None:
        return
    from copy import deepcopy
    from nanocursor.memory.context import owned
    from nanocursor.memory.budget import estimate
    from nanocursor.memory.recall import RecallOutcome
    service = ctx.agent.memory_recall
    service.last = RecallOutcome(status="empty")
    service.injected = service.deduplicated = 0
    previous = deepcopy(ctx.conversation)
    ctx.conversation.history = [m for m in ctx.conversation.history
                                if not (owned(m) and (not dynamic_only or m.memory_context["kind"] == "recall"))]
    service.context_tokens = sum(estimate(m.content) for m in ctx.conversation.history if owned(m))
    if previous.history == ctx.conversation.history:
        return
    ctx.conversation.reset_usage_anchor()
    from nanocursor.application.session import SessionPersistence
    from nanocursor.events import MemoryContextChanged

    SessionPersistence(ctx.session, ctx.conversation, ctx.ui.add_system_message).commit_memory(
        MemoryContextChanged(previous),
    )
    ctx.ui.refresh_status()


MEMORY_COMMAND = Command(
    name="memory",
    description="记忆管理",
    usage="/memory [list | clear | edit | recall status|off|local|model | consolidate status|on|off]",
    type=CommandType.LOCAL,
    handler=handle_memory,
)
