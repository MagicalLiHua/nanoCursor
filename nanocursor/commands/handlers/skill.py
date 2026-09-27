from __future__ import annotations

from typing import TYPE_CHECKING

from nanocursor.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from nanocursor.skills.loader import SkillLoader


async def handle_skill(ctx: CommandContext) -> None:
    parts = ctx.args.strip().split(maxsplit=1)
    subcmd = parts[0] if parts else "list"
    sub_args = parts[1] if len(parts) > 1 else ""

    loader: SkillLoader | None = ctx.config.get("skill_loader")
    if loader is None:
        ctx.ui.add_system_message("Skill 系统未初始化")
        return

    if subcmd == "list":
        _handle_list(ctx, loader)
    elif subcmd == "info":
        _handle_info(ctx, loader, sub_args)
    elif subcmd == "reload":
        await _handle_reload(ctx, loader)
    else:
        ctx.ui.add_system_message(
            f"未知子命令：{subcmd}\n用法：/skill list | /skill info <name> | /skill reload"
        )


def _handle_list(ctx: CommandContext, loader: SkillLoader) -> None:
    catalog = loader.get_catalog()
    lines = ["已加载的 Skill：" if catalog else "没有可运行的 Skill"]
    for name, desc in catalog:
        source = loader.get_source_label(name)
        lines.append(f"  {name:<20} {desc}  [{source}]")
    for name, error in getattr(loader, "diagnostics", {}).items():
        lines.append(f"  {name} [不可用]: {error}")
    ctx.ui.add_system_message("\n".join(lines))


def _handle_info(ctx: CommandContext, loader: SkillLoader, name: str) -> None:
    if not name:
        ctx.ui.add_system_message("用法：/skill info <name>")
        return

    skill = loader.get(name)
    if skill is None:
        ctx.ui.add_system_message(f"Skill 不可用：{name}\n{getattr(loader, 'diagnostics', {}).get(name, '未找到定义')}")
        return

    source = loader.get_source_label(name)
    lines = [
        f"Skill: {skill.name}",
        f"Description: {skill.description}",
        f"Mode: {skill.mode}",
        f"Context: {skill.context}",
        f"Model: {skill.model or '(default)'}",
        f"Provider: {skill.provider or '(inherit)'}",
        f"Tools: {list(skill.tools) if skill.tools is not None else '(inherit fork-safe subset)'}",
        f"Source: {source}",
        f"Path: {skill.source_path or '(builtin)'}",
        f"Directory: {skill.is_directory}",
    ]
    executor = ctx.config.get("skill_executor")
    if executor:
        try:
            lines.append(executor.describe(skill))
        except ValueError as exc:
            lines.append(f"Unavailable: {exc}")
    ctx.ui.add_system_message("\n".join(lines))


async def _handle_reload(ctx: CommandContext, loader: SkillLoader) -> None:
    skills = loader.reload()

    registry = ctx.config.get("registry")
    if registry is not None:
        from nanocursor.commands.handlers.skill_register import register_skill_commands
        register_skill_commands(registry, loader, ctx.config.get("skill_executor"))

    # 刷新 agent 的 skill catalog，这样 LLM 能看到新增的 skill
    agent = ctx.agent
    if agent is not None:
        catalog = loader.get_catalog()
        if catalog:
            lines = ["You can use the following Skills:", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "If the user's request matches a Skill, call LoadSkill to activate it."
            )
            agent.set_skill_catalog("\n".join(lines))
        else:
            agent.set_skill_catalog("")

    ctx.ui.add_system_message(f"已重新加载 {len(skills)} 个 Skill")


SKILL_COMMAND = Command(
    name="skill",
    description="管理 Skill 技能包",
    usage="/skill list | /skill info <name> | /skill reload",
    type=CommandType.LOCAL,
    handler=handle_skill,
    aliases=["skills"],
)
