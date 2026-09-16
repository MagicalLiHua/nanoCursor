from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from nanocursor.agent import Agent
from nanocursor.agents.parser import AgentDef
from nanocursor.agents.trace import TraceManager
from nanocursor.conversation import ConversationManager
from nanocursor.hooks.engine import HookEngine
from nanocursor.hooks.models import Action, Hook
from nanocursor.skills.executor import SkillExecutor
from nanocursor.skills.parser import SkillDef
from nanocursor.tools import create_default_registry
from nanocursor.tools.agent_tool import AgentTool, AgentToolParams, FORK_QUERY_SOURCE
from nanocursor.tools.base import ToolCallComplete
from nanocursor.tools.runtime import ToolRuntimeContext, bind_runtime
from nanocursor.worktree.models import Worktree
from test_execution_boundaries import ScriptClient, Probe, registry_for, agent, checker


def setup_spawn(tmp_path, *, definition_isolation="", readonly=False, client=None):
    registry = create_default_registry()
    parent = agent(tmp_path, client=client, registry=registry)
    parent._current_conversation = ConversationManager()
    parent._current_conversation.add_user_message("parent history")
    definition = AgentDef(agent_type="test", when_to_use="test", permission_mode="bypassPermissions",
                          isolation=definition_isolation, tools=["ReadFile"] if readonly else [])
    loader = NS(get=lambda name: definition)
    tasks = NS(launch=Mock(return_value="background-id"))
    trace = TraceManager()

    async def create(name, base):
        path = tmp_path / name
        path.mkdir()
        return Worktree(name, str(path), "branch", base, "commit")

    worktrees = NS(create=AsyncMock(side_effect=create),
                   auto_cleanup=AsyncMock(return_value=NS(kept=False)))
    tool = AgentTool(loader, tasks, trace, parent, enable_fork=True, worktree_manager=worktrees)
    registry.register(tool)
    return tool, parent, tasks, worktrees, trace


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"subagent_type": "test"},
                                   {"subagent_type": "test", "isolation": "worktree"}, {"team_name": "team"}])
@pytest.mark.parametrize("guard", ["fork", "runtime"])
async def test_nested_spawn_rejected_before_any_route_or_side_effect(tmp_path, params, guard):
    tool, parent, tasks, worktrees, trace = setup_spawn(tmp_path)
    if guard == "fork":
        tool.query_source = FORK_QUERY_SOURCE
    with bind_runtime(ToolRuntimeContext(tmp_path, "child", spawn_allowed=guard != "runtime")):
        result = await tool.execute(AgentToolParams(prompt="task", description="task", **params))
    assert result.is_error and "cannot create" in result.output
    assert not tasks.launch.called and not worktrees.create.called and not trace._nodes


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,defined,readonly,expected", [
    ("worktree", "", False, "worktree"), ("none", "worktree", False, "none"),
    (None, "worktree", True, "worktree"), (None, "", False, "worktree"),
    (None, "", True, "none"), ("auto", "worktree", True, "none"),
])
async def test_isolation_precedence_and_background_policy(tmp_path, requested, defined, readonly, expected):
    tool, parent, tasks, worktrees, trace = setup_spawn(tmp_path, definition_isolation=defined, readonly=readonly)
    result = await tool.execute(AgentToolParams(prompt="task", description="task", subagent_type="test",
                                               run_in_background=True, isolation=requested))
    assert not result.is_error
    child = tasks.launch.call_args.kwargs["agent"]
    assert child.spawn_allowed is False
    assert worktrees.create.await_count == (expected == "worktree")
    assert (child.work_dir != parent.work_dir) == (expected == "worktree")
    assert next(iter(trace._nodes.values())).isolation == expected
    assert child.registry.get("Agent") is None


@pytest.mark.asyncio
async def test_two_writable_forks_get_distinct_worktrees(tmp_path):
    tool, parent, tasks, worktrees, trace = setup_spawn(tmp_path)
    for _ in range(2):
        result = await tool.execute(AgentToolParams(prompt="task", description="task"))
        assert not result.is_error
    children = [call.kwargs["agent"] for call in tasks.launch.call_args_list]
    assert children[0].work_dir != children[1].work_dir != parent.work_dir
    assert all(child.sandbox_root == child.work_dir for child in children)
    nested = ToolCallComplete("nested", "Agent", {"prompt": "x", "description": "x", "subagent_type": "test"})
    result = await children[0]._execute_tool_noninteractive(nested)
    assert result.is_error and "cannot create" in result.output


@pytest.mark.asyncio
async def test_foreground_worktree_writes_only_child_directory_and_cleans_up(tmp_path):
    client = ScriptClient([ToolCallComplete("write", "WriteFile", {"file_path": "output.txt", "content": "child"})])
    tool, parent, tasks, worktrees, trace = setup_spawn(tmp_path, client=client)
    result = await tool.execute(AgentToolParams(prompt="task", description="task", subagent_type="test", isolation="worktree"))
    assert not result.is_error
    assert not (tmp_path / "output.txt").exists()
    child_root = Path(next(iter(trace._nodes.values())).work_dir)
    assert (child_root / "output.txt").read_text() == "child"
    worktrees.auto_cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_isolation_failure_does_not_fall_back_to_parent(tmp_path):
    tool, parent, tasks, worktrees, trace = setup_spawn(tmp_path)
    worktrees.create.side_effect = RuntimeError("no git repository")
    result = await tool.execute(AgentToolParams(prompt="task", description="task", subagent_type="test", isolation="worktree"))
    assert result.is_error and not tasks.launch.called and not trace._nodes


@pytest.mark.asyncio
async def test_skill_fork_inherits_rejecting_hook_and_permissions(tmp_path):
    probe = Probe()
    hook = Hook(id="deny", event="pre_tool_use", reject=True, action=Action(type="prompt", message="blocked"))
    parent = agent(tmp_path, ScriptClient([ToolCallComplete("probe", "Probe", {})]), registry_for(probe),
                   permission_checker=checker(tmp_path), hook_engine=HookEngine([hook]))
    await SkillExecutor(parent, parent.client, "anthropic").execute_fork(
        SkillDef(name="test", description="test", mode="fork", context="none"), "task")
    assert probe.count == 0 and hook.executed
    assert parent.spawn_allowed


@pytest.mark.asyncio
async def test_real_worktree_agent_modifies_only_its_checkout(tmp_path):
    import subprocess
    from nanocursor.worktree.manager import WorktreeManager

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init"], ["config", "user.email", "test@example.invalid"], ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "test fixture"], cwd=repo, check=True, capture_output=True)
    client = ScriptClient([
        ToolCallComplete("r", "ReadFile", {"file_path": "seed.txt"}),
        ToolCallComplete("w", "WriteFile", {"file_path": "child.txt", "content": "only in child"}),
        ToolCallComplete("p", "Bash", {"command": "pwd"}),
    ])
    tool, parent, tasks, _, trace = setup_spawn(repo, client=client)
    manager = WorktreeManager(str(repo))
    tool._worktree_manager = manager
    result = await tool.execute(AgentToolParams(prompt="task", description="task", subagent_type="test", isolation="worktree"))
    assert not result.is_error
    child = Path(next(iter(trace._nodes.values())).work_dir)
    assert child != repo and (child / "child.txt").read_text() == "only in child"
    assert not (repo / "child.txt").exists()
    assert "Worktree preserved" in result.output


@pytest.mark.asyncio
async def test_worktree_is_cleaned_up_if_child_construction_fails(tmp_path, monkeypatch):
    tool, parent, tasks, worktrees, trace = setup_spawn(tmp_path)
    monkeypatch.setattr("nanocursor.agent.Agent", Mock(side_effect=RuntimeError("constructor failed")))
    result = await tool.execute(AgentToolParams(prompt="task", description="task", subagent_type="test", isolation="worktree"))
    assert result.is_error and "constructor failed" in result.output
    worktrees.auto_cleanup.assert_awaited_once()
    assert not tasks.launch.called


@pytest.mark.asyncio
async def test_skill_fork_cannot_skip_parent_permissions_without_hooks(tmp_path):
    probe = Probe()
    parent = agent(tmp_path, ScriptClient([ToolCallComplete("probe", "Probe", {})]), registry_for(probe),
                   permission_checker=checker(tmp_path))
    await SkillExecutor(parent, parent.client, "anthropic").execute_fork(
        SkillDef(name="test", description="test", mode="fork", context="none"), "task")
    assert probe.count == 0


@pytest.mark.asyncio
async def test_background_worktree_cleanup_runs_on_completion(tmp_path):
    import asyncio
    from nanocursor.agents.task_manager import TaskManager
    tool, parent, _, worktrees, trace = setup_spawn(tmp_path)
    tasks = TaskManager()
    tool._task_manager = tasks
    result = await tool.execute(AgentToolParams(prompt="task", description="task", subagent_type="test", run_in_background=True))
    assert not result.is_error
    running = list(tasks._async_tasks.values())
    await asyncio.wait_for(asyncio.gather(*running), 2)
    worktrees.auto_cleanup.assert_awaited_once()
    assert all(bg.status == "completed" for bg in tasks._tasks.values())
