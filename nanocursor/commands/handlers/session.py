from __future__ import annotations

from nanocursor.commands.ports import command_services
from nanocursor.commands.registry import Command, CommandContext, CommandType
from nanocursor.conversation import ConversationManager


async def handle_session(ctx: CommandContext) -> None:
    sm = ctx.session_manager
    if sm is None:
        ctx.ui.add_system_message("会话管理器未初始化")
        return

    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""
    sessions = command_services(ctx).sessions
    if (sub == "new" or (sub == "resume" and len(parts) > 1)) and sessions.has_active_tasks():
        ctx.ui.add_system_message("Wait for background tasks to finish before switching sessions.")
        return

    if sub == "":
        if ctx.session:
            m = ctx.session.meta
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            ctx.ui.add_system_message(
                f"当前会话: {m.id}\n"
                f"  标题: {m.title or '(未命名)'}\n"
                f"  消息: {m.message_count} 条\n"
                f"  Token: {m.total_tokens:,}\n"
                f"  最后活跃: {ts}"
            )
        else:
            ctx.ui.add_system_message("当前没有活跃会话")
        return

    if sub == "list":
        metas = sm.list()
        if not metas:
            ctx.ui.add_system_message("没有已保存的会话。")
            return
        lines: list[str] = ["会话列表："]
        for m in metas[:10]:
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            title = m.title or "(未命名)"
            lines.append(f"  {m.id}  {title}  [{m.message_count} msgs, {ts}]")
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "resume":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            metas = sm.list()
            if not metas:
                ctx.ui.add_system_message("没有已保存的会话。")
                return
            lines: list[str] = ["可恢复的会话（使用 /session resume <id> 或 /session resume <序号>）："]
            for i, m in enumerate(metas[:15], 1):
                ts = m.last_active.strftime("%Y-%m-%d %H:%M")
                title = m.title or "(未命名)"
                lines.append(f"  {i}. [{m.id}]  {title}  ({m.message_count} msgs, {ts})")
            ctx.ui.add_system_message("\n".join(lines))
            sessions.set_resume_candidates(tuple(m.id for m in metas[:15]))
            return
        candidates = sessions.get_resume_candidates()
        if session_id.isdigit():
            if not candidates:
                ctx.ui.add_system_message("请先运行 /session resume 查看候选会话。")
                return
            idx = int(session_id) - 1
            if not 0 <= idx < len(candidates):
                ctx.ui.add_system_message(f"编号无效，请选择 1–{len(candidates)}，或重新查看列表。")
                return
            session_id = candidates[idx]
        prepare = command_services(ctx).sessions.prepare_session_change
        if prepare:
            await prepare()
        result = sm.resume(session_id)
        if result is None:
            ctx.ui.add_system_message(f"会话未找到: {session_id}")
            return
        if ctx.session:
            ctx.session.close()
        sessions.set_resume_candidates(())
        command_services(ctx).sessions.set_session(result.session)
        conv = ConversationManager()
        for msg in result.messages:
            conv.history.append(msg)
        command_services(ctx).sessions.set_conversation(conv)
        await command_services(ctx).sessions.render_restored(result.messages)
        ctx.ui.add_system_message(
            f"会话已恢复: {session_id} ({result.session.meta.message_count} msgs)"
        )


    elif sub == "new":
        prepare = command_services(ctx).sessions.prepare_session_change
        if prepare:
            await prepare()
        new_session = sm.create()
        if ctx.session:
            ctx.session.close()
        command_services(ctx).sessions.set_session(new_session)
        command_services(ctx).sessions.set_conversation(ConversationManager())
        command_services(ctx).sessions.clear_chat()
        ctx.ui.add_system_message(f"新会话已创建: {new_session.session_id}")

    elif sub == "delete":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            ctx.ui.add_system_message("用法: /session delete <id>")
            return
        if ctx.session and ctx.session.session_id == session_id:
            ctx.ui.add_system_message("不能删除当前活跃的会话。")
            return
        prepare = command_services(ctx).sessions.prepare_session_change
        if prepare:
            await prepare()
        if sm.delete(session_id):
            ctx.ui.add_system_message(f"会话已删除: {session_id}")
        else:
            ctx.ui.add_system_message(f"会话未找到: {session_id}")


    else:
        ctx.ui.add_system_message(
            "用法: /session [list | resume <id> | new | delete <id>]"
        )


SESSION_COMMAND = Command(
    name="session",
    description="会话管理",
    usage="/session [list | resume <id> | new | delete <id>]",
    type=CommandType.LOCAL,
    handler=handle_session,
)
