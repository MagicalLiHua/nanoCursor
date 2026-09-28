"""Task launch barriers, historical notifications and child cancellation facts."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanocursor.agents.task_manager import TaskManager, persist_notification_message
from nanocursor.memory.session import SessionManager
from nanocursor.recovery import RecoveryRuntime, RecoveryStorageError, RecoveryStore
from nanocursor.recovery.runtime import current_runtime
from nanocursor.teams.spawn_inprocess import spawn_inprocess_teammate


@pytest.fixture
def runtime(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(workspace, store=store)
    session = SessionManager(str(workspace), recovery=runtime).create()
    yield runtime
    session.close()
    runtime.close()
    store.close()


def fake_agent(runtime, run=None):
    return SimpleNamespace(
        recovery=runtime, work_dir=runtime.workspace.root,
        run_to_completion=run or AsyncMock(return_value="finished result"),
        worktree_cleanup=None, total_input_tokens=3, total_output_tokens=2,
        team_name="", _team_manager=None,
    )


def test_pseudo_adoption_cannot_replay_an_existing_description(runtime):
    tasks = TaskManager()
    agent = fake_agent(runtime)
    with pytest.raises(RuntimeError, match="rerunning its description"):
        tasks.adopt_running(agent, "database mutation")
    assert not tasks.list_tasks()
    agent.run_to_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_intent_barrier_precedes_create_task(runtime, monkeypatch):
    tasks = TaskManager()
    agent = fake_agent(runtime)
    def fail(*args, **kwargs):
        raise RecoveryStorageError("injected intent failure")
    monkeypatch.setattr(runtime, "begin_operation", fail)
    with pytest.raises(RecoveryStorageError, match="intent failure"):
        tasks.launch(agent, "never start")
    assert not tasks.list_tasks() and not tasks._async_tasks
    agent.run_to_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_notification_survives_lost_ui_queue_and_is_idempotent(runtime):
    tasks = TaskManager()
    task_id = tasks.launch(fake_agent(runtime), "do work", session_id=runtime.session_id)
    await tasks._async_tasks[task_id]
    bg = tasks.get(task_id)
    operation = runtime.store.rows("SELECT * FROM operations WHERE operation_id=?", (bg.operation_id,))[0]
    assert operation["state"] == "completed"
    payload = json.loads(operation["result"])
    assert payload["result"] == "finished result"
    # No asynchronous user record is inserted in the middle of a live tool batch.
    assert not runtime.store.projection_records(runtime.session_key)
    tasks.poll_completed()
    runtime.reconcile_session()
    runtime.reconcile_session()
    records = runtime.store.projection_records(runtime.session_key)
    notifications = [record for record in records if record.get("record_id") == "notification_" + bg.operation_id]
    assert len(notifications) == 1 and "finished result" in notifications[0]["content"]
    assert persist_notification_message(runtime.session, bg.notification_message)
    assert persist_notification_message(runtime.session, bg.notification_message)
    assert len(runtime.store.projection_records(runtime.session_key)) == 1


@pytest.mark.asyncio
async def test_completed_parent_launch_keeps_child_lifecycle_live(runtime):
    started, release = asyncio.Event(), asyncio.Event()
    async def run(*_):
        started.set()
        await release.wait()
        return "done"
    with runtime.activate():
        parent = runtime.begin_operation("tool", "Agent", {})
        with runtime.operation_context(parent):
            tasks = TaskManager()
            task_id = tasks.launch(fake_agent(runtime, run), "child")
        runtime.finish_operation(parent, "launched")
    await started.wait()
    bg = tasks.get(task_id)
    row = runtime.store.rows("SELECT * FROM operations WHERE operation_id=?", (bg.operation_id,))[0]
    assert row["state"] == "intent" and row["parent_operation_id"] == parent
    release.set()
    await tasks._async_tasks[task_id]


@pytest.mark.asyncio
async def test_observed_task_result_is_durable_before_cleanup(runtime):
    cleaning, release = asyncio.Event(), asyncio.Event()
    async def cleanup():
        cleaning.set()
        await release.wait()
        return " kept"
    agent = fake_agent(runtime)
    agent.worktree_cleanup = cleanup
    tasks = TaskManager()
    task_id = tasks.launch(agent, "work")
    await cleaning.wait()
    bg = tasks.get(task_id)
    observed = runtime.store.get_metadata("background_observation", bg.operation_id)
    assert observed["result"] == "finished result"
    assert runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (bg.operation_id,))[0]["state"] == "intent"
    release.set()
    await tasks._async_tasks[task_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("leave_effect_unconfirmed", [False, True])
async def test_background_cancel_distinguishes_stopped_coroutine_and_unknown_effect(runtime, leave_effect_unconfirmed):
    started = asyncio.Event()
    async def run(*_):
        ledger = current_runtime()
        if leave_effect_unconfirmed:
            ledger.begin_operation("mcp", "database_write", {})
        started.set()
        await asyncio.Event().wait()
    tasks = TaskManager()
    task_id = tasks.launch(fake_agent(runtime, run), "wait")
    await started.wait()
    assert await tasks.cancel_and_wait(task_id)
    bg = tasks.get(task_id)
    assert bg.status == "cancelled"
    row = runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (bg.operation_id,))[0]
    assert row["state"] == ("outcome_unknown" if leave_effect_unconfirmed else "completed")
    assert runtime.store.get_metadata("background_result", bg.operation_id)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_before_background_coroutine_has_started_is_not_dispatched(runtime):
    tasks = TaskManager()
    agent = fake_agent(runtime)
    task_id = tasks.launch(agent, "queued")
    handle = tasks._async_tasks[task_id]
    tasks.cancel(task_id)
    await asyncio.gather(handle, return_exceptions=True)
    bg = tasks.get(task_id)
    row = runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (bg.operation_id,))[0]
    assert row["state"] == "interrupted"
    agent.run_to_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_skill_prepared_outside_agent_run_inherits_runtime_and_cannot_replay(runtime, monkeypatch):
    from test_skill_execution import executor, definition
    exe, client, _ = executor(__import__('pathlib').Path(runtime.workspace.root), monkeypatch)
    exe.agent.recovery = runtime
    invocation = exe.prepare_fork(definition(tools=()), "review")
    operation_id = invocation.prepared[1]
    assert runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (operation_id,))[0]["state"] == "intent"
    result = await exe.execute_fork(invocation.skill, "review", invocation=invocation)
    assert result.status == "success" and result.notification_message
    assert runtime.store.get_metadata("skill_result", operation_id)["text"] == "RESULT"
    requests = len(client.requests)
    with pytest.raises(ValueError, match="cannot be replayed"):
        await exe.execute_fork(invocation.skill, "review", invocation=invocation)
    assert len(client.requests) == requests
    runtime.reconcile_session()
    assert len([r for r in runtime.store.projection_records(runtime.session_key) if r.get("record_id") == "notification_" + operation_id]) == 1


@pytest.mark.asyncio
async def test_team_actor_records_each_observed_turn_and_terminal_lifecycle(runtime):
    async def run(*args, **kwargs):
        assert current_runtime() is runtime
        return "team result"
    actor = spawn_inprocess_teammate(fake_agent(runtime, run), "task", "worker", team_name="test")
    assert runtime.store.rows("SELECT state FROM operations WHERE kind='team_actor'")[0]["state"] == "intent"
    assert await actor.task == "team result"
    operation_id = actor.prepared[1]
    assert runtime.store.get_metadata("team_observation", operation_id)["result"] == "team result"
    assert runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (operation_id,))[0]["state"] == "completed"
