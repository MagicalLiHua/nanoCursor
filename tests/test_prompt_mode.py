import json
import sys
from unittest.mock import AsyncMock

import pytest

from nanocursor.__main__ import _run_prompt, main
from nanocursor.client import LLMClient
from nanocursor.config import AppConfig, ProviderConfig
from nanocursor.permissions import PermissionMode
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallComplete
from nanocursor.workspace import WorkspaceContext


class ScriptClient(LLMClient):
    def __init__(self, turns):
        self.turns = iter(turns)

    async def stream(self, *args, **kwargs):
        events = next(self.turns, [TextDelta("done"), StreamEnd("end_turn")])
        for event in events:
            if isinstance(event, Exception):
                raise event
            yield event


@pytest.fixture
def prompt_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("nanocursor.client.resolve_context_window", AsyncMock())
    config = AppConfig([ProviderConfig("test", "openai-compat", "http://127.0.0.1:1", "test", api_key="synthetic")])
    def use(turns):
        monkeypatch.setattr("nanocursor.client.create_client", lambda _: ScriptClient(turns))
    return config, WorkspaceContext.resolve(tmp_path), use


def events(capsys):
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert sum(e["type"] == "result" for e in output) == 1
    assert output[-1]["type"] == "result"
    return output


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [PermissionMode.DEFAULT, PermissionMode.PLAN])
async def test_prompt_stops_for_unapproved_write(prompt_env, tmp_path, capsys, mode):
    config, workspace, use = prompt_env
    use([[ToolCallComplete("write", "WriteFile", {"file_path": "blocked.txt", "content": "no"}), StreamEnd("end_turn")]])
    status = await _run_prompt(config, mode, None, "task", "stream-json", workspace=workspace)
    output = events(capsys)
    assert status == 1 and output[-1]["stop_reason"] == "permission_required"
    assert output[-1]["is_error"] and not (tmp_path / "blocked.txt").exists()


@pytest.mark.asyncio
async def test_prompt_stop_cancels_later_writes_in_same_batch(prompt_env, tmp_path, capsys):
    config, workspace, use = prompt_env
    rules = tmp_path / ".nanocursor/permissions.yaml"
    rules.parent.mkdir()
    rules.write_text('- rule: "WriteFile(*blocked.txt)"\n  effect: ask')
    use([[ToolCallComplete(name, "WriteFile", {"file_path": name, "content": "no"})
          for name in ["blocked.txt", "later.txt"]] + [StreamEnd("end_turn")]])
    status = await _run_prompt(config, PermissionMode.ACCEPT_EDITS, None, "task", "stream-json", workspace=workspace)
    assert status == 1
    assert not (tmp_path / "blocked.txt").exists() and not (tmp_path / "later.txt").exists()
    assert events(capsys)[-1]["stop_reason"] == "permission_required"


@pytest.mark.asyncio
async def test_prompt_explicit_accept_edits_still_works(prompt_env, tmp_path, capsys):
    config, workspace, use = prompt_env
    use([[ToolCallComplete("write", "WriteFile", {"file_path": "allowed.txt", "content": "yes"}), StreamEnd("end_turn")]])
    assert await _run_prompt(config, PermissionMode.ACCEPT_EDITS, None, "task", "stream-json", workspace=workspace) == 0
    assert (tmp_path / "allowed.txt").read_text() == "yes"
    assert events(capsys)[-1]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_summary_matches_tool_ids(prompt_env, tmp_path, capsys):
    config, workspace, use = prompt_env
    (tmp_path / "exists.txt").write_text("ok")
    use([[ToolCallComplete("bad", "ReadFile", {"file_path": "missing.txt"}),
          ToolCallComplete("good", "ReadFile", {"file_path": "exists.txt"}), StreamEnd("end_turn")]])
    assert await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace) == 0
    calls = events(capsys)[-1]["tool_calls"]
    assert {c["tool_id"]: c["is_error"] for c in calls} == {"bad": True, "good": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown_tools", "runtime_error"])
async def test_terminal_failure_produces_nonzero_status(prompt_env, capsys, failure):
    config, workspace, use = prompt_env
    turns = ([[RuntimeError("synthetic provider failure")]] if failure == "runtime_error" else
             [[ToolCallComplete(f"missing-{i}", "NoSuchTool", {}), StreamEnd("end_turn")] for i in range(3)])
    use(turns)
    assert await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace) == 1
    output = events(capsys)
    assert output[-1]["stop_reason"] == failure and output[-1]["exit_code"] == 1


@pytest.mark.asyncio
async def test_compact_warning_does_not_turn_success_into_failure(prompt_env, capsys, monkeypatch):
    config, workspace, use = prompt_env
    use([[TextDelta("answer"), StreamEnd("end_turn")]])
    monkeypatch.setattr("nanocursor.agent.auto_compact", AsyncMock(return_value="temporary summary failure"))
    assert await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace) == 0
    output = events(capsys)
    assert next(e for e in output if e["type"] == "error")["fatal"] is False
    assert output[-1]["result"] == "answer" and not output[-1]["is_error"]


def test_cli_propagates_failure_exit_code(prompt_env, tmp_path, capsys, monkeypatch):
    config, _, use = prompt_env
    use([[RuntimeError("synthetic provider failure")]])
    monkeypatch.setattr("nanocursor.__main__.load_config", lambda **kwargs: config)
    monkeypatch.setattr(sys, "argv", ["nanocursor", "--cwd", str(tmp_path), "-p", "task", "--output-format", "stream-json"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1 and events(capsys)[-1]["exit_code"] == 1
