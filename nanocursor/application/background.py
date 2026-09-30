from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nanocursor.agents.loader import AgentLoader
from nanocursor.teams.manager import TeamManager
from nanocursor.tools.agent_tool import AgentTool
from nanocursor.tools.team_create import TeamCreateTool
from nanocursor.tools.team_delete import TeamDeleteTool

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.agents.task_manager import TaskManager
    from nanocursor.agents.trace import TraceManager
    from nanocursor.config import ProviderConfig
    from nanocursor.worktree.manager import WorktreeManager


@dataclass(frozen=True)
class BackgroundOptions:
    enable_fork: bool = False
    enable_teams: bool = False
    enable_verification: bool = False
    teammate_mode: str = "in-process"
    interactive: bool = False
    enable_coordinator: bool = False


@dataclass(frozen=True)
class BackgroundServices:
    loader: AgentLoader
    teams: TeamManager
    agent_tool: AgentTool


def assemble_background(agent: Agent, provider: ProviderConfig, worktrees: WorktreeManager,
                        tasks: TaskManager, traces: TraceManager, options: BackgroundOptions) -> BackgroundServices:
    loader = AgentLoader(agent.work_dir, enable_verification=options.enable_verification)
    loader.load_all()
    teams = TeamManager(worktree_manager=worktrees, trace_manager=traces, task_manager=tasks)
    tool = AgentTool(
        agent_loader=loader, task_manager=tasks, trace_manager=traces, parent_agent=agent,
        enable_fork=options.enable_fork, enable_teams=options.enable_teams,
        provider_config=provider, worktree_manager=worktrees, team_manager=teams,
    )
    agent.registry.register(tool)
    if options.enable_teams:
        agent.registry.register(TeamCreateTool(
            team_manager=teams, parent_agent=agent, teammate_mode=options.teammate_mode,
            is_interactive=options.interactive, enable_coordinator_mode=options.enable_coordinator,
            enable_teams=options.enable_teams,
        ))
        agent.registry.register(TeamDeleteTool(team_manager=teams, parent_agent=agent))
    return BackgroundServices(loader, teams, tool)
