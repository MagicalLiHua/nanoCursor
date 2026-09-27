"""Exercise extension boundaries through real agents, tasks, and Git worktrees."""
from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from nanocursor.agents.task_manager import TaskManager
from nanocursor.agents.trace import TraceManager
from nanocursor.client import LLMClient
from nanocursor.commands.handlers.skill_register import register_skill_commands
from nanocursor.commands.registry import CommandContext, CommandRegistry
from nanocursor.conversation import ConversationManager
from nanocursor.hooks import HookConfigError, HookEngine, load_hooks
from nanocursor.hooks.models import HookContext
from nanocursor.permissions import RuleEngine
from nanocursor.skills.executor import SkillExecutor
from nanocursor.skills.parser import SkillDef
from nanocursor.teams.manager import TeamManager
from nanocursor.teams.models import AgentTeam, TeammateInfo
from nanocursor.tools import create_default_registry
from nanocursor.tools.agent_tool import AgentTool, AgentToolParams
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete
from nanocursor.tools.team_create import TeamCreateParams, TeamCreateTool
from nanocursor.tools.team_delete import TeamDeleteParams, TeamDeleteTool
from nanocursor.worktree.manager import WorktreeManager
from test_execution_boundaries import ScriptClient, agent, checker


@pytest.mark.asyncio
async def test_command_hook_arguments_remain_data_before_denied_tool(tmp_path):
    marker = tmp_path / "marker"
    output = tmp_path / "context.json"
    value = f'$(touch {marker}) `touch {marker}`; "quote"\n中文.txt'
    script = tmp_path / "capture.py"
    script.write_text("import json, os, sys\njson.dump({'env': os.environ['NANOCURSOR_HOOK_FILE_PATH'], 'stdin': json.load(sys.stdin)}, open(sys.argv[1], 'w'))\n")
    command = " ".join(map(shlex.quote, [sys.executable, str(script), str(output)]))
    hooks = load_hooks([{"id": "capture", "event": "pre_tool_use", "action": {
        "type": "command", "command": command, "input": "context-json",
    }}])
    rules = tmp_path / "rules.yaml"
    rules.write_text('- rule: "ReadFile(*)"\n  effect: deny\n')
    parent = agent(tmp_path, ScriptClient(), create_default_registry(), hook_engine=HookEngine(hooks),
                   permission_checker=checker(tmp_path, rules=RuleEngine(project_rules_path=rules)))
    result = await parent._execute_tool_noninteractive(ToolCallComplete("read", "ReadFile", {"file_path": value}))
    assert result.is_error and not marker.exists()
    data = json.loads(output.read_text())
    expected_path = str(tmp_path / value)
    assert data["env"] == data["stdin"]["file_path"] == expected_path
    assert data["stdin"]["tool_args"]["file_path"] == expected_path
    assert data["stdin"]["schema_version"] == 1


@pytest.mark.parametrize("template", ["$FILE_PATH", "${EVENT}", "$MESSAGE", "$ERROR", "$TOOL_NAME", "$TOOL_ARGS.command"])
def test_old_hook_templates_fail_with_source_and_id(template):
    with pytest.raises(HookConfigError, match=r"project.yaml: hook 'security'.*legacy"):
        load_hooks([{"id": "security", "event": "pre_tool_use", "reject": True,
                     "action": {"type": "command", "command": f'printf "%s" "{template}"'}}], source="project.yaml")
    assert load_hooks([{"event": "startup", "action": {"type": "command", "command": 'printf "%s" "$PATH" | cat'}}])


@pytest.mark.asyncio
async def test_async_hook_shutdown_awaits_process_cleanup(tmp_path):
    marker = tmp_path / "should-not-exist"
    engine = HookEngine(load_hooks([{"event": "post_tool_use", "async": True,
        "action": {"type": "command", "command": f"sleep 0.2; touch {shlex.quote(str(marker))}"}}]))
    await engine.run_hooks("post_tool_use", HookContext())
    await asyncio.sleep(0.03)
    assert await engine.shutdown()
    await asyncio.sleep(0.25)
    assert not marker.exists()


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def setup_team(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("initial")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    tasks = TaskManager()
    trees = WorktreeManager(str(repo))
    teams = TeamManager(trees, task_manager=tasks)
    team = teams.create_team("review", "lead", teammate_mode="in-process")
    return repo, tasks, trees, teams, team


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["clean", "dirty", "untracked", "commit"])
async def test_team_delete_preserves_real_git_results(tmp_path, monkeypatch, state):
    repo, tasks, trees, teams, team = setup_team(tmp_path, monkeypatch)
    wt = await trees.create("worker", "HEAD")
    path = Path(wt.path)
    if state == "dirty":
        (path / "tracked.txt").write_text("edited")
    if state in ("untracked", "commit"):
        (path / "result.txt").write_text("result")
    if state == "commit":
        git(path, "add", ".")
        git(path, "commit", "-m", "worker result")
    commit = git(path, "rev-parse", "HEAD")
    member = TeammateInfo("worker", "worker-id", "test", "fake", wt.path, "in-process", False, branch=wt.branch)
    teams.register_member(team.name, member)
    result = await TeamDeleteTool(teams).execute(TeamDeleteParams(team_name=team.name))
    assert not result.is_error and "retained" in result.output
    assert path.exists() and wt.path in git(repo, "worktree", "list", "--porcelain")
    assert git(path, "rev-parse", "HEAD") == commit
    assert git(repo, "rev-parse", wt.branch) == commit
    if state == "dirty":
        assert (path / "tracked.txt").read_text() == "edited"
    if state in ("untracked", "commit"):
        assert (path / "result.txt").read_text() == "result"
    restored = TeamManager().get_team(team.name)
    assert restored.status == "closed" and restored.members[0].worktree_path == wt.path
    assert (await teams.close_team(team.name)).status == "closed"


@pytest.mark.asyncio
async def test_close_waits_real_idle_task_and_preserves_result(tmp_path, monkeypatch):
    _, tasks, trees, teams, team = setup_team(tmp_path, monkeypatch)
    wt = await trees.create("worker", "HEAD")
    child = agent(Path(wt.path))
    child.team_name, child._team_manager, child.agent_id = team.name, teams, "worker-id"
    member = TeammateInfo("worker", child.agent_id, "test", "fake", wt.path, "in-process", True, branch=wt.branch)
    teams.register_member(team.name, member)
    member.task_id = tasks.launch(child, "test", session_id="old-session")
    for _ in range(100):
        if tasks.get(member.task_id).status == "idle":
            break
        await asyncio.sleep(0.01)
    assert tasks.get(member.task_id).status == "idle"
    assert tasks.has_active_tasks(team_name=team.name)
    result = await teams.close_team(team.name)
    assert result.status == "closed" and not tasks.has_active_tasks()
    assert Path(wt.path).exists() and result.members[0].result
    assert tasks.poll_completed("new-session") == []
    assert [t.session_id for t in tasks.poll_completed("old-session")] == ["old-session"]


@pytest.mark.asyncio
async def test_task_timeout_does_not_recancel_cleanup_or_claim_done():
    started, cleanup_started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def run(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await finish.wait()
    child = NS(run_to_completion=run, total_input_tokens=0, total_output_tokens=0, worktree_cleanup=None, team_name="")
    tasks = TaskManager()
    task_id = tasks.launch(child, "work")
    await started.wait()
    assert not await tasks.cancel_and_wait(task_id, timeout=0.01)
    await cleanup_started.wait()
    assert tasks.has_active_tasks() and tasks.cancel(task_id)
    finish.set()
    assert await tasks.cancel_and_wait(task_id)
    assert tasks.get(task_id).status == "cancelled"


@pytest.mark.asyncio
async def test_closed_or_disabled_team_cannot_create_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    teams, trees, tasks = TeamManager(), NS(create=AsyncMock()), TaskManager()
    parent = agent(tmp_path)
    create = TeamCreateTool(teams, parent)
    assert (await create.execute(TeamCreateParams(team_name="disabled"))).is_error
    assert not (tmp_path / "home" / "teams").exists()
    tool = AgentTool(NS(), tasks, TraceManager(), parent, worktree_manager=trees, team_manager=teams)
    assert "team_name" not in tool.get_schema()["input_schema"]["properties"]
    params = AgentToolParams(prompt="x", description="x", team_name="disabled")
    assert (await tool.execute(params)).is_error
    team = teams.create_team("closed", parent.agent_id, teammate_mode="in-process")
    await teams.close_team(team.name)
    tool._enable_teams = True
    assert (await tool.execute(params.model_copy(update={"team_name": team.name}))).is_error
    trees.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_spawn_race_retains_created_tree_without_launch(tmp_path, monkeypatch):
    _, tasks, trees, teams, team = setup_team(tmp_path, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    original_create = trees.create
    async def slow_create(*args):
        started.set()
        await release.wait()
        return await original_create(*args)
    monkeypatch.setattr(trees, "create", slow_create)
    tool = AgentTool(NS(), tasks, TraceManager(), agent(tmp_path), worktree_manager=trees, team_manager=teams, enable_teams=True)
    pending = asyncio.create_task(tool.execute(AgentToolParams(prompt="x", description="x", team_name=team.name)))
    await started.wait()
    closing = asyncio.create_task(teams.close_team(team.name))
    await asyncio.sleep(0)
    assert team.status == "closing"
    release.set()
    result = await pending
    assert result.is_error and not tasks.has_active_tasks()
    assert (await closing).status == "closed"
    assert len(team.members) == 1 and Path(team.members[0].worktree_path).exists()


@pytest.mark.asyncio
async def test_team_close_timeout_keeps_recovery_record_and_worktree(tmp_path, monkeypatch):
    _, tasks, trees, teams, team = setup_team(tmp_path, monkeypatch)
    wt = await trees.create("worker", "HEAD")
    started, finish = asyncio.Event(), asyncio.Event()
    async def run(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await finish.wait()
    child = NS(run_to_completion=run, total_input_tokens=0, total_output_tokens=0,
               worktree_cleanup=None, team_name=team.name, agent_id="worker-id")
    task_id = tasks.launch(child, "work")
    member = TeammateInfo("worker", "worker-id", "test", "fake", wt.path, "in-process", True,
                          branch=wt.branch, task_id=task_id)
    teams.register_member(team.name, member)
    await started.wait()
    try:
        assert (await teams.close_team(team.name, timeout=0.01)).status == "closing"
        assert AgentTeam.load(team.config_path).status == "closing"
        assert tasks.has_active_tasks() and Path(wt.path).exists()
    finally:
        finish.set()
        assert (await teams.close_team(team.name)).status == "closed"


@pytest.mark.asyncio
async def test_team_record_failure_reports_failure_without_removing_results(tmp_path, monkeypatch):
    _, _, trees, teams, team = setup_team(tmp_path, monkeypatch)
    wt = await trees.create("worker", "HEAD")
    output = Path(wt.path) / "result.txt"
    output.write_text("precious")
    teams.register_member(team.name, TeammateInfo("worker", "worker", "test", "fake", wt.path, "in-process", False))
    monkeypatch.setattr(team, "save", Mock(side_effect=OSError("disk full")))
    result = await TeamDeleteTool(teams).execute(TeamDeleteParams(team_name=team.name))
    assert result.is_error and "disk full" in result.output
    assert output.read_text() == "precious" and Path(team.config_path).exists()
    assert team.status == "closing"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["none", "recent", "full"])
async def test_skill_handler_passes_current_independent_snapshot(tmp_path, mode):
    captured = []
    class CaptureClient(LLMClient):
        async def stream(self, conversation, system="", tools=None):
            captured.extend(m.content for m in conversation.history)
            yield TextDelta("done")
            yield StreamEnd("end_turn")
    client = CaptureClient()
    parent = agent(tmp_path, client)
    stale = ConversationManager()
    stale.add_user_message("STALE PRIVATE HISTORY")
    parent._conversation = parent._current_conversation = stale
    current = ConversationManager()
    for i in range(7):
        current.add_user_message(f"CURRENT {i}")
    skill = SkillDef(name="snapshot-test", description="snapshot", prompt_body="SKILL", mode="fork", context=mode)
    loader = NS(get_catalog=lambda: [(skill.name, skill.description)], get=lambda _name: skill)
    registry, owned = CommandRegistry(), []
    executor = SkillExecutor(parent, client, "anthropic")
    register_skill_commands(registry, loader, executor)
    ctx = CommandContext("", parent, current, None, None, None, Mock(), {
        "skill_loader": loader, "register_owned_task": owned.append, "session_id": "current",
        "is_session_current": lambda session: session == "current",
    })
    await registry.find(skill.name).handler(ctx)
    current.history[0].content = "MUTATED AFTER SPAWN"
    await asyncio.gather(*owned)
    joined = "\n".join(captured)
    assert "STALE" not in joined and "MUTATED" not in joined
    assert "SKILL" in joined
    if mode == "none":
        assert "CURRENT" not in joined
    elif mode == "recent":
        assert "CURRENT 0" not in joined and "CURRENT 2" in joined
    else:
        assert all(f"CURRENT {i}" in joined for i in range(7))
