from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from nanocursor.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.skills.loader import SkillLoader


class LoadSkillParams(BaseModel):
    name: str = Field(description="The name of the skill to load")
    args: str = Field(default="", description="Arguments for the Skill task")


class LoadSkill(Tool):
    name = "LoadSkill"
    description = (
        "Run a configured Skill by name. Inline returns and activates its SOP; "
        "fork runs independently with its configured model and tool subset and returns its result."
    )
    params_model = LoadSkillParams
    category = "read"
    is_concurrency_safe = False
    is_system_tool = True


    def __init__(self) -> None:
        self._loader: SkillLoader | None = None
        self._agent: Agent | None = None
        self._executor = None


    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent


    def set_executor(self, executor) -> None:
        self._executor = executor

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, LoadSkillParams)

        if self._loader is None or self._agent is None:
            return ToolResult(
                output="Error: LoadSkill not properly initialized",
                is_error=True,
            )

        skill = self._loader.get(params.name)
        if skill is None:
            available = ", ".join(n for n, _ in self._loader.get_catalog())
            return ToolResult(
                output=f"Error: unknown skill '{params.name}'. {getattr(self._loader, 'diagnostics', {}).get(params.name, '')} Available skills: {available}",
                is_error=True,
            )

        from nanocursor.skills.executor import SkillExecutor
        from nanocursor.tools.runtime import current_runtime
        runtime = current_runtime()
        if runtime and (not runtime.spawn_allowed or runtime.agent_id != self._agent.agent_id):
            return ToolResult("LoadSkill belongs to the main session and cannot be called by a child", True)
        executor = self._executor or SkillExecutor(self._agent, self._agent.client, self._agent.protocol)
        try:
            if skill.mode == "fork":
                conversation = getattr(self._agent, "_current_conversation", None)
                if conversation is None:
                    return ToolResult("Skill fork needs the current conversation", True)
                snapshot = executor.snapshot_context(skill.context, conversation)
                result = await executor.execute_fork(skill, params.args, context_messages=snapshot)
                return ToolResult(result.display(), result.status != "success")
            prompt = executor.execute_inline(skill, params.args)
            return ToolResult(f"# Skill: {skill.name}\n\n" + prompt)
        except (ValueError, OSError) as exc:
            return ToolResult(f"Skill configuration error: {exc}", True)
