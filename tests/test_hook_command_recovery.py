"""A failed output reader must not leave an untracked shell running."""
from __future__ import annotations

import os

import pytest

from nanocursor.agent import Agent
from nanocursor.hooks.engine import HookEngine
from nanocursor.hooks.models import Action, Hook, HookContext
from nanocursor.recovery import RecoveryRequired, RecoveryRuntime, RecoveryStore
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import ToolCallComplete
from nanocursor.tools.bash import Bash, _spawn_owned_shell, _terminate_process_group


@pytest.fixture
def host(tmp_path, monkeypatch):
    work = tmp_path / "project"
    work.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(work, store=store)
    yield runtime, work
    runtime.close()
    store.close()


async def execute(kind, runtime, work):
    if kind == "hook":
        engine = HookEngine([Hook(id="reader", event="turn_end",
                                  action=Action(type="command", command="sleep 20"))])
        with runtime.activate():
            await engine.run_hooks("turn_end", HookContext(event_name="turn_end"))
    else:
        registry = ToolRegistry()
        registry.register(Bash())
        agent = Agent(object(), registry, "anthropic", str(work), recovery=runtime)
        await agent._execute_single_tool_direct(ToolCallComplete("call", "Bash", {"command": "sleep 20"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["hook", "bash"])
async def test_reader_failure_stops_process_and_requires_recovery(host, monkeypatch, kind):
    runtime, work = host
    processes = []

    async def reader_failure(*_):
        raise OSError("output pipe failed before result was observed")

    async def spawn(command, **kwargs):
        proc = await _spawn_owned_shell(command, **kwargs)
        processes.append(proc)
        assert proc.returncode is None
        if kind == "hook":
            proc.communicate = reader_failure
        return proc

    module = "nanocursor.hooks.executors" if kind == "hook" else "nanocursor.tools.bash"
    monkeypatch.setattr(module + "._spawn_owned_shell", spawn)
    if kind == "bash":
        monkeypatch.setattr(module + "._capture_output", reader_failure)
    try:
        await execute(kind, runtime, work)
        assert len(processes) == 1
        assert processes[0].returncode is not None
        if os.name == "posix":
            with pytest.raises(ProcessLookupError):
                os.killpg(processes[0].pid, 0)
        pending = runtime.pending()
        assert len(pending) == 1 and pending[0]["state"] == "outcome_unknown"
        assert "output pipe failed" in pending[0]["observation"]
        with pytest.raises(RecoveryRequired):
            runtime.begin_operation("tool", "next mutation")
    finally:
        for proc in processes:
            if proc.returncode is None:
                await _terminate_process_group(proc)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["hook", "bash"])
async def test_spawn_failure_does_not_claim_unknown_execution(host, monkeypatch, kind):
    runtime, work = host

    async def cannot_spawn(*_, **__):
        raise OSError("process could not be created")

    module = "nanocursor.hooks.executors" if kind == "hook" else "nanocursor.tools.bash"
    monkeypatch.setattr(module + "._spawn_owned_shell", cannot_spawn)
    await execute(kind, runtime, work)
    assert runtime.pending() == []
    assert runtime.store.rows("SELECT state FROM operations")[0]["state"] == "completed"
