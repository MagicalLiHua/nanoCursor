"""Prompt mode reports completion only after durable ownership is released."""
from __future__ import annotations

import asyncio
import builtins
import json
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanocursor.__main__ import _run_prompt
from nanocursor.agent import Agent
from nanocursor.agents.task_manager import TaskManager
from nanocursor.memory.session import Session, SessionManager
from nanocursor.permissions import PermissionMode
from nanocursor.recovery import RecoveryRuntime, RecoveryStorageError, RecoveryStore
from nanocursor.teams.manager import TeamManager
from test_prompt_mode import prompt_env


def reports(capsys):
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert sum(row["type"] == "result" for row in output) == 1
    assert output[-1]["type"] == "result"
    return output


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["session", "runtime"])
async def test_close_failure_precedes_single_terminal_error(prompt_env, monkeypatch, capsys, failure):
    config, workspace, use = prompt_env
    use([])
    order = []
    session_close, runtime_close, original_print = Session.close, RecoveryRuntime.close, builtins.print

    def close_session(session):
        order.append("session")
        session_close(session)
        if failure == "session":
            raise OSError("session flush failed")

    def close_runtime(runtime):
        order.append("runtime")
        runtime_close(runtime)
        if failure == "runtime":
            raise RecoveryStorageError("owner close commit failed")

    def print_report(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith('{"type": "result"'):
            order.append("result")
        original_print(*args, **kwargs)

    monkeypatch.setattr(Session, "close", close_session)
    monkeypatch.setattr(RecoveryRuntime, "close", close_runtime)
    monkeypatch.setattr(builtins, "print", print_report)
    status = await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace)
    output = reports(capsys)
    assert status == 4
    assert output[-1]["stop_reason"] == "recovery_storage_error"
    assert output[-1]["is_error"] and output[-1]["exit_code"] == 4
    assert order == ["session", "runtime", "result"]


@pytest.mark.asyncio
async def test_gate_failure_keeps_original_result_when_close_also_fails(prompt_env, monkeypatch, capsys):
    config, workspace, _ = prompt_env
    previous = RecoveryRuntime.acquire(workspace.active_cwd)
    operation = previous.begin_operation("tool", "remote mutation")
    previous.mark_unknown(operation, "reply lost")
    previous.close()
    runtime_close = RecoveryRuntime.close

    def close_runtime(runtime):
        runtime_close(runtime)
        raise RecoveryStorageError("secondary close failure")

    monkeypatch.setattr(RecoveryRuntime, "close", close_runtime)
    monkeypatch.setattr("nanocursor.client.create_client", lambda _: pytest.fail("gate must run first"))
    status = await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace)
    output = reports(capsys)
    assert status == 3 and output[-1]["stop_reason"] == "recovery_required"
    assert "secondary close failure" in output[0]["message"]


@pytest.mark.asyncio
async def test_initialization_error_and_cleanup_error_emit_one_result(prompt_env, monkeypatch, capsys):
    config, workspace, _ = prompt_env
    closed = []
    session_close, runtime_close = Session.close, RecoveryRuntime.close

    def cannot_initialize(_):
        raise RecoveryStorageError("initial durable setup failed")

    def close_session(session):
        closed.append("session")
        session_close(session)
        raise OSError("secondary session flush failed")

    def close_runtime(runtime):
        closed.append("runtime")
        runtime_close(runtime)

    monkeypatch.setattr("nanocursor.client.create_client", cannot_initialize)
    monkeypatch.setattr(Session, "close", close_session)
    monkeypatch.setattr(RecoveryRuntime, "close", close_runtime)
    status = await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace)
    output = reports(capsys)
    assert status == 4 and output[-1]["stop_reason"] == "recovery_storage_error"
    assert "initial durable setup failed" in output[0]["message"]
    assert "secondary session flush failed" in output[0]["message"]
    assert closed == ["session", "runtime"]


@pytest.mark.asyncio
async def test_team_notification_keeps_durable_identity_and_user_checkpoint(prompt_env, monkeypatch, capsys):
    config, workspace, use = prompt_env
    use([])
    managers, sources, completed = [], [], []
    original_init, original_run = TaskManager.__init__, Agent.run

    def init_tasks(manager):
        original_init(manager)
        managers.append(manager)

    def init_teams(manager, **kwargs):
        manager._teams = {"test": SimpleNamespace(status="active")}

    async def close_team(manager, name):
        manager._teams[name].status = "closed"
        return manager._teams[name]

    async def run(agent, conversation, *, source="user", **kwargs):
        sources.append(source)
        try:
            async with aclosing(original_run(agent, conversation, source=source, **kwargs)) as stream:
                async for event in stream:
                    yield event
        finally:
            if source == "user":
                child = SimpleNamespace(recovery=agent.recovery, work_dir=agent.work_dir,
                                        run_to_completion=AsyncMock(return_value="durable worker output"),
                                        team_name="", total_input_tokens=0, total_output_tokens=0)
                task_id = managers[0].launch(child, "child task", name="worker")
                await managers[0]._async_tasks[task_id]
                await asyncio.sleep(0)
                completed.append(managers[0]._tasks[task_id])

    monkeypatch.setattr(TaskManager, "__init__", init_tasks)
    monkeypatch.setattr(TeamManager, "__init__", init_teams)
    monkeypatch.setattr(TeamManager, "close_team", close_team)
    monkeypatch.setattr(TeamManager, "drain_lead_mailbox", lambda *_, **__: [])
    monkeypatch.setattr(Agent, "run", run)
    status = await _run_prompt(config, PermissionMode.DEFAULT, None, "task", "stream-json", workspace=workspace)
    assert status == 0 and not reports(capsys)[-1]["is_error"]
    assert sources == ["user", "notification"]

    store = RecoveryStore()
    runtime = RecoveryRuntime.acquire(workspace.active_cwd, store=store)
    resumed = None
    try:
        session = store.rows("SELECT session_id,session_key FROM sessions")[0]
        resumed = SessionManager(str(workspace.workspace_dir), recovery=runtime).resume(session["session_id"])
        records = store.projection_records(session["session_key"])
        notices = [row for row in records if row["type"] == "user" and "durable worker output" in row["content"]]
        assert len(notices) == 1
        assert notices[0]["record_id"] == "notification_" + completed[0].operation_id
        assert len(store.rows("SELECT checkpoint_id FROM checkpoint_records")) == 1
    finally:
        if resumed:
            resumed.session.close()
        runtime.close()
        store.close()
