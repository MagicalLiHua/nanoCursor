from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from nanocursor.tools.base import Tool, ToolResult
from nanocursor.tools.runtime import current_runtime

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.teams.manager import TeamManager


class TeamDeleteParams(BaseModel):
    team_name: str


class TeamDeleteTool(Tool):
    name = "TeamDelete"
    description = (
        "Stop an experimental Agent Team and retain all worktrees, branches, "
        "results and recovery records. No project files are deleted."
    )
    params_model = TeamDeleteParams
    category = "command"
    is_concurrency_safe = False


    def __init__(self, team_manager: TeamManager, parent_agent: Agent | None = None) -> None:
        self._team_manager = team_manager
        self._parent_agent = parent_agent


    async def execute(self, params: BaseModel) -> ToolResult:
        p: TeamDeleteParams = params  # type: ignore[assignment]
        context = current_runtime()
        if context is not None and not context.spawn_allowed:
            return ToolResult("Sub-agents cannot close the parent Teams.", True)

        from nanocursor.teams.manager import TeamError

        try:
            team = await self._team_manager.close_team(p.team_name)
        except TeamError as e:
            return ToolResult(output=str(e), is_error=True)
        except Exception as e:
            return ToolResult(output=f"Failed to delete team: {e}", is_error=True)

        retained = "\n".join(
            f"- {member.name}: {member.worktree_path} (branch {member.branch or 'unknown'})"
            for member in team.members if member.worktree_path
        )
        if team.status != "closed":
            return ToolResult(
                f"Team '{p.team_name}' is still closing: {team.close_error}\n"
                f"Retained worktrees:\n{retained}\nRecovery record: {team.config_path}", True,
            )
        coordinator_note = ""
        if self._parent_agent and self._parent_agent.coordinator_mode:
            # 只有在所有 Team 都被删除后才恢复全量工具，避免多 Team 场景下
            # 删掉其中一个就提前解除限制。
            if not self._team_manager.list_teams():
                full_registry = getattr(self._parent_agent, '_full_registry', None)
                if full_registry is not None:
                    self._parent_agent.registry = full_registry
                    self._parent_agent._full_registry = None
                self._parent_agent.coordinator_mode = False
                coordinator_note = "\nCoordinator Mode deactivated: full tools restored."

        return ToolResult(output=f"Team '{p.team_name}' closed; all worktrees and branches retained.\n{retained}\nRecovery record: {team.config_path}{coordinator_note}")
