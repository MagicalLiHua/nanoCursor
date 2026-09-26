from __future__ import annotations

from rich.markup import escape

from nanocursor.commands.registry import Command, CommandContext, CommandType


async def handle_approval(ctx: CommandContext) -> None:
    controller = getattr(ctx.agent, "approval_controller", None) if ctx.agent else None
    if controller is None:
        ctx.ui.add_system_message("自动审批仅支持主 Agent 的交互界面。")
        return
    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else "status"
    if sub in {"on", "off"}:
        controller.config.mode = "smart" if sub == "on" else "manual"
        controller.revision += 1
        ctx.ui.refresh_status()
    elif sub == "provider":
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            ctx.ui.add_system_message("用法: /approval provider <名称|main>\n可选: " +
                                      ", ".join(p.name for p in controller.providers))
            return
        if name != "main" and len([p for p in controller.providers if p.name == name]) != 1:
            ctx.ui.add_system_message("审批 provider 不存在或重名，配置未变更。")
            return
        controller.config.provider = None if name == "main" else name
        controller.revision += 1
        ctx.ui.refresh_status()
    elif sub not in {"status", ""}:
        ctx.ui.add_system_message("用法: /approval [status|on|off|provider <名称|main>]")
        return

    active = controller.active(ctx.agent.permission_mode)
    state = "生效" if active else ("当前权限模式下暂停" if controller.config.mode == "smart" else "关闭")
    bash = ctx.agent.registry.get("Bash")
    isolated = bool(bash and bash.execution_details(ctx.agent.work_dir, ctx.agent.sandbox_root)["sandbox_active"])
    lines = [f"Bash 自动审批: {state}", f"  审批模型: {controller.route}",
             f"  OS 沙箱: {'生效' if isolated else '未生效'}",
             f"  总超时: {controller.config.timeout_seconds:g}s",
             "  命令与直接用户输入会发送给以上审批模型；仅批准本次调用。",
             "  外部操作保留人工授权；规则放行不属于模型审批。",
             "  此命令只修改当前运行；默认值可在 config.yaml 的 approval 中配置。"]
    if controller.last_result:
        result = controller.last_result
        lines.extend([f"  最近审查: {result.source} · {result.elapsed:.2f}s · {result.route}",
                      f"  原因: {result.reason}",
                      f"  用量: {result.input_tokens} input / {result.output_tokens} output"])
    # UI add_system_message renders plain text for this command's model output.
    ctx.ui.add_system_message(escape("\n".join(lines)))


APPROVAL_COMMAND = Command(
    name="approval", description="Bash 自动审批：开关、模型和状态", type=CommandType.LOCAL,
    usage="/approval [status|on|off|provider <名称|main>]", handler=handle_approval,
)
