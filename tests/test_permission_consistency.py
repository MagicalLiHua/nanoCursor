from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from nanocursor.agent import Agent
from nanocursor.agents.trace import TraceManager
from nanocursor.config import load_config
from nanocursor.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from nanocursor.teams.manager import TeamManager
from nanocursor.tools import create_default_registry
from nanocursor.tools.agent_tool import AgentTool, AgentToolParams
from nanocursor.tools.base import ToolCallComplete
from nanocursor.validator import ConfigError


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", ["deny", "ask"])
async def test_team_respects_parent_restrictions(tmp_path, monkeypatch, effect):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    rules = tmp_path / "permissions.yaml"
    rules.write_text(f'- rule: "WriteFile(*)"\n  effect: {effect}\n')
    checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(tmp_path)),
                                RuleEngine(user_rules_path=rules), mode=PermissionMode.BYPASS)
    parent = Agent(Mock(), create_default_registry(), "openai-compat", str(tmp_path), permission_checker=checker)
    teams = TeamManager()
    team = teams.create_team("test", parent.agent_id)
    child_dir = tmp_path / "worktree"
    child_dir.mkdir()
    worktrees = NS(create=AsyncMock(return_value=NS(path=str(child_dir))))
    tasks = NS(launch=Mock(return_value="task-id"))
    tool = AgentTool(NS(), tasks, TraceManager(), parent, worktree_manager=worktrees, team_manager=teams)
    result = await tool.execute(AgentToolParams(prompt="task", description="test", team_name=team.name))
    assert not result.is_error
    child = tasks.launch.call_args.kwargs["agent"]
    result = await child._execute_tool_noninteractive(ToolCallComplete("write", "WriteFile", {
        "file_path": "blocked.txt", "content": "must not be written",
    }))
    assert result.is_error and "denied" in result.output.lower()
    assert not (child_dir / "blocked.txt").exists()


@pytest.mark.parametrize("content", [
    "broken: [", "{}", "", "- nope", '- rule: "ReadFile(*)"\n  effect: nope',
    '- rule: 42\n  effect: deny', '- rule: "invalid"\n  effect: deny',
    '- rule: "ReadFile(*)"\n  effect: deny\n  typo: true',
])
def test_malformed_rules_fail_closed_on_reload(tmp_path, content):
    path = tmp_path / "permissions.yaml"
    path.write_text('- rule: "ReadFile(*)"\n  effect: deny')
    engine = RuleEngine(user_rules_path=path)
    assert engine.evaluate("ReadFile", "example.txt") == "deny"
    path.write_text(content)
    with pytest.raises(ConfigError, match="ermission"):
        engine.evaluate("ReadFile", "example.txt")


def test_rules_directory_is_not_silently_ignored(tmp_path):
    with pytest.raises(ConfigError, match="readable file"):
        RuleEngine(user_rules_path=tmp_path).validate()


def test_startup_rejects_malformed_user_rules(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(home))
    (home / "config.yaml").write_text('''providers:
  - name: test
    protocol: openai-compat
    base_url: http://127.0.0.1:1/v1
    model: synthetic
    api_key: synthetic
''')
    (home / "permissions.yaml").write_text("broken: [")
    with pytest.raises(ConfigError, match="permission rules"):
        load_config(work_dir=tmp_path)
