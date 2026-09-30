from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nanocursor.commands.ports import command_services
from nanocursor.commands.registry import Command, CommandContext, CommandRegistry, CommandType

if TYPE_CHECKING:
    from nanocursor.skills.executor import SkillExecutor
    from nanocursor.skills.loader import SkillLoader

log = logging.getLogger(__name__)

_REGISTERED_SKILL_NAMES: set[str] = set()


def register_skill_commands(
    registry: CommandRegistry,
    loader: SkillLoader,
    executor: SkillExecutor | None = None,
) -> None:
    for name in list(_REGISTERED_SKILL_NAMES):
        if registry.find(name) is not None:
            registry._commands.pop(name, None)
            registry._alias_map = {
                k: v for k, v in registry._alias_map.items() if v != name
            }
        _REGISTERED_SKILL_NAMES.discard(name)

    for skill_name, skill_desc in loader.get_catalog():
        if registry.find(skill_name) is not None:
            continue

        s_name = skill_name
        s_desc = skill_desc


        def make_handler(name: str) -> callable:


            async def handler(ctx: CommandContext) -> None:
                exe = command_services(ctx).skill_executor if executor is None else executor
                if exe is None:
                    ctx.ui.add_system_message("Skill 执行器未初始化")
                    return

                skill_loader: SkillLoader | None = command_services(ctx).skill_loader
                if skill_loader is None:
                    ctx.ui.add_system_message("Skill 加载器未初始化")
                    return

                skill = skill_loader.get(name)
                if skill is None:
                    ctx.ui.add_system_message(f"Skill 不可用：{name}\n{getattr(skill_loader, 'diagnostics', {}).get(name, '未找到定义')}")
                    return

                if skill.mode == "fork":
                    if ctx.conversation is None:
                        ctx.ui.add_system_message("Skill fork 需要当前会话")
                        return
                    snapshot = exe.snapshot_context(skill.context, ctx.conversation)
                    try:
                        invocation = exe.prepare_fork(skill, ctx.args, context_messages=snapshot)
                    except (ValueError, OSError) as exc:
                        ctx.ui.add_system_message(f"Skill 配置错误: {exc}")
                        return
                    session_id = command_services(ctx).session_id
                    is_current = command_services(ctx).is_session_current
                    ctx.ui.add_system_message(f"⏳ Running {name} skill...")


                    async def _run_fork() -> None:
                        try:
                            result = await exe.execute_fork(skill, ctx.args, invocation=invocation)
                            if not asyncio.current_task().cancelling() and is_current(session_id):
                                ctx.ui.add_system_message(
                                    f"[{name} skill result]\n{result.display()}"
                                )
                                publish = command_services(ctx).queue_skill_result
                                if publish:
                                    publish(session_id, ctx.conversation, name, result)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            if is_current(session_id):
                                ctx.ui.add_system_message(
                                    f"Skill {name} failed: {e}"
                                )

                    register_task = command_services(ctx).register_owned_task
                    if register_task is None:
                        await _run_fork()
                    else:
                        register_task(asyncio.create_task(_run_fork()))
                else:
                    try:
                        prompt = exe.execute_inline(skill, ctx.args)
                    except ValueError as exc:
                        ctx.ui.add_system_message(f"Skill 配置错误: {exc}")
                        return
                    ctx.ui.add_system_message(
                        f"skill({name})\nSuccessfully loaded skill"
                    )
                    trigger = ctx.args if ctx.args else f"/{name}"
                    ctx.ui.send_skill_message(trigger, name, prompt)

            return handler

        cmd = Command(
            name=s_name,
            description=f"{s_desc} [skill]",
            usage=f"/{s_name} [args]",
            type=CommandType.PROMPT,
            handler=make_handler(s_name),
        )

        try:
            registry.register_sync(cmd)
            _REGISTERED_SKILL_NAMES.add(s_name)
        except ValueError as e:
            log.warning("Cannot register skill command '%s': %s", s_name, e)
