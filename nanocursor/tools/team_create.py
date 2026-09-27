from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from nanocursor.tools.base import Tool, ToolResult
from nanocursor.tools.runtime import current_runtime

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.teams.manager import TeamManager


class TeamCreateParams(BaseModel):
    team_name: str
    description: str = ""


class TeamCreateTool(Tool):
    name = "TeamCreate"
    description = (
        "Create an experimental team. Requires enable_teams: true. "
        "Team messaging and persistent follow-up are experimental. "
        "Use Agent(team_name=..., name=...) to create isolated teammates. "
        "TeamDelete stops tasks and retains all worktrees and results."
    )
    params_model = TeamCreateParams
    category = "command"
    is_concurrency_safe = False


    def __init__(
        self,
        team_manager: TeamManager,
        parent_agent: Agent,
        teammate_mode: str = "",
        is_interactive: bool = True,
        enable_coordinator_mode: bool = False,
        enable_teams: bool = False,
    ) -> None:
        self._enable_teams = enable_teams
        self._team_manager = team_manager
        self._parent_agent = parent_agent
        self._teammate_mode = teammate_mode
        self._is_interactive = is_interactive
        self._enable_coordinator_mode = enable_coordinator_mode


    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamCreateParams = params  # type: ignore[assignment]
        if not self._enable_teams:
            return ToolResult("Experimental Teams are disabled. Set enable_teams: true to enable them.", True)
        context = current_runtime()
        if ((context is not None and not context.spawn_allowed)
                or not getattr(self._parent_agent, "spawn_allowed", True)):
            return ToolResult("Sub-agents cannot create Teams.", True)

        from nanocursor.teams.backend_detect import BackendDetectionError

        try:
            backend = self._team_manager.detect_backend(
                self._teammate_mode, self._is_interactive
            )
        except BackendDetectionError as e:
            return ToolResult(output=str(e), is_error=True)

        try:
            team = self._team_manager.create_team(
                name=p.team_name,
                lead_agent_id=self._parent_agent.agent_id,
                description=p.description,
                teammate_mode=self._teammate_mode,
                is_interactive=self._is_interactive,
            )
        except Exception as e:
            return ToolResult(output=f"Failed to create team: {e}", is_error=True)

        coordinator_note = ""
        from nanocursor.teams.coordinator import is_coordinator_mode
        if is_coordinator_mode(self._enable_coordinator_mode):
            from nanocursor.agents.tool_filter import apply_coordinator_filter
            self._parent_agent._team_manager = self._team_manager
            if not self._parent_agent.coordinator_mode:
                # 只在从"未限制"切换到"限制"时才保存全量注册表快照，
                # 避免第二个 Team 创建时把已过滤的注册表误当成全量注册表存起来。
                self._parent_agent._full_registry = self._parent_agent.registry
                self._parent_agent.registry = apply_coordinator_filter(self._parent_agent.registry)
                self._parent_agent.coordinator_mode = True
            coordinator_note = "\nCoordinator Mode activated: tools narrowed to dispatch-only."

        return ToolResult(
            output=(
                f"Team '{team.name}' created successfully.\n"
                f"Backend: {backend.value}\n"
                f"Config: {team.config_path}\n"
                f"Use Agent tool with team_name='{team.name}' to spawn teammates."
                f"{coordinator_note}"
            )
        )
