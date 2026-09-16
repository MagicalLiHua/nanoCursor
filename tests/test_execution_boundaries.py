"""Regressions for tool permission, scheduling, cancellation and workspace boundaries."""
from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel

from nanocursor.agent import Agent, PermissionRequest, PermissionResponse
from nanocursor.cache import FileCache
from nanocursor.client import LLMClient
from nanocursor.conversation import ConversationManager
from nanocursor.hooks.engine import HookEngine
from nanocursor.hooks.models import Action, Hook
from nanocursor.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from nanocursor.tools import ToolRegistry, create_default_registry
from nanocursor.tools.base import StreamEnd, TextDelta, Tool, ToolCallComplete, ToolResult
from nanocursor.tools.bash import Bash, Params as BashParams
from nanocursor.tools.glob import Glob
from nanocursor.tools.grep import Grep
from nanocursor.tools.read_file import ReadFile, Params as ReadParams
from nanocursor.tools.runtime import current_runtime


class ScriptClient(LLMClient):
    def __init__(self, calls=(), hold=None):
        self.calls, self.hold, self.round = calls, hold, 0

    async def stream(self, conversation, system="", tools=None):
        self.round += 1
        if self.round == 1:
            for call in self.calls:
                yield call
            if self.hold:
                await self.hold.wait()
        else:
            yield TextDelta("done")
        yield StreamEnd("end_turn")


class EmptyParams(BaseModel):
    pass


class Probe(Tool):
    name, description, category = "Probe", "Regression test tool", "command"
    params_model = EmptyParams
    is_concurrency_safe = False

    def __init__(self):
        self.count = 0

    async def execute(self, params):
        self.count += 1
        return ToolResult("executed")


def registry_for(tool):
    registry = ToolRegistry()
    registry.register(tool)
    return registry


def checker(root, mode=PermissionMode.DEFAULT, rules=None):
    return PermissionChecker(DangerousCommandDetector(), PathSandbox(str(root)), rules or RuleEngine(), mode=mode)


def agent(root, client=None, registry=None, **kwargs):
    return Agent(client or ScriptClient(), registry or ToolRegistry(), "anthropic", work_dir=str(root),
                 inject_environment_context=False, **kwargs)


async def drive(a, conv=None):
    conv = conv if conv is not None else ConversationManager()
    if not conv.history:
        conv.add_user_message("test")
    events = []
    async for event in a.run(conv):
        events.append(event)
        if isinstance(event, PermissionRequest):
            event.future.set_result(PermissionResponse.ALLOW)
    return conv, events


def assert_closed(conv):
    pending = set()
    for message in conv.history:
        if message.tool_uses:
            assert not pending
            pending.update(t.tool_use_id for t in message.tool_uses)
        for result in message.tool_results:
            assert result.tool_use_id in pending
            pending.remove(result.tool_use_id)
    assert not pending


@pytest.mark.parametrize("command", ["cat ~/.ssh/id_rsa", "env python -c 'print(1)'", "find . -delete",
                                    "git branch -D x", "ls .\nprintf x", "echo x & touch x", "npx anything"])
def test_unsafe_prefixes_require_approval(tmp_path, command):
    assert checker(tmp_path).check(Bash(), {"command": command}).effect == "ask"


@pytest.mark.parametrize("effect", ["deny", "ask"])
@pytest.mark.parametrize("sandbox_enabled", [False, True])
def test_explicit_rules_precede_safe_shortcut(tmp_path, effect, sandbox_enabled):
    rule_file = tmp_path / "rules.yaml"
    rule_file.write_text(f"- rule: 'Bash(*)'\n  effect: {effect}\n")
    check = checker(tmp_path, rules=RuleEngine(project_rules_path=rule_file))
    check.sandbox_enabled = sandbox_enabled
    assert check.check(Bash(), {"command": "git status"}).effect == effect


@pytest.mark.parametrize("tool", [Grep(), Glob()])
def test_search_checks_base_path(tmp_path, tool):
    check = checker(tmp_path)
    outside = "/outside-nanocursor-test"
    assert not check.sandbox.check(outside)[0]
    assert check.check(tool, {"pattern": "needle", "path": outside}).effect == "ask"


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", ["allow", "ask", "deny"])
async def test_rejecting_hook_covers_all_permission_paths(tmp_path, effect):
    probe = Probe()
    hook = Hook(id="reject", event="pre_tool_use", action=Action(type="prompt", message="blocked"), reject=True)
    engine = HookEngine([hook])
    engine.run_pre_tool_hooks = AsyncMock(wraps=engine.run_pre_tool_hooks)
    check = checker(tmp_path)
    check.check = Mock(return_value=SimpleNamespace(effect=effect, reason="test"))
    a = agent(tmp_path, ScriptClient([ToolCallComplete("id", "Probe", {})]), registry_for(probe),
              permission_checker=check, hook_engine=engine)
    conv, events = await drive(a)
    assert probe.count == 0
    assert engine.run_pre_tool_hooks.await_count == 1
    assert not any(isinstance(e, PermissionRequest) for e in events)
    assert_closed(conv)


@pytest.mark.asyncio
async def test_approval_runs_each_hook_once(tmp_path):
    probe = Probe()
    engine = HookEngine()
    engine.run_pre_tool_hooks = AsyncMock(return_value=None)
    engine.run_hooks = AsyncMock()
    a = agent(tmp_path, ScriptClient([ToolCallComplete("id", "Probe", {})]), registry_for(probe),
              permission_checker=checker(tmp_path), hook_engine=engine)
    await drive(a)
    assert probe.count == 1
    assert engine.run_pre_tool_hooks.await_count == 1
    assert sum(call.args[0] == "post_tool_use" for call in engine.run_hooks.await_args_list) == 1


@pytest.mark.asyncio
async def test_invalid_json_never_executes_optional_parameter_tool(tmp_path):
    probe = Probe()
    engine = HookEngine()
    engine.run_pre_tool_hooks = AsyncMock(return_value=None)
    a = agent(tmp_path, registry=registry_for(probe), hook_engine=engine)
    result = await a._execute_tool_noninteractive(ToolCallComplete("bad", "Probe", {}, "{broken", "invalid JSON"))
    assert result.is_error and "{broken" in result.output
    assert probe.count == 0
    engine.run_pre_tool_hooks.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("before_history", [True, False])
async def test_cancel_closes_history_and_stops_owned_tasks(tmp_path, before_history):
    started, release = asyncio.Event(), asyncio.Event()
    probe = Probe()
    owned = []

    async def wait(params):
        owned.append(asyncio.current_task())
        started.set()
        await release.wait()
        probe.count += 1
        return ToolResult("side effect")

    probe.execute = wait
    a = agent(tmp_path, ScriptClient([ToolCallComplete("cancel-id", "Probe", {})],
                                    asyncio.Event() if before_history else None), registry_for(probe))
    conv = ConversationManager()
    task = asyncio.create_task(drive(a, conv))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(t.done() for t in owned) and probe.count == 0
    assert_closed(conv)
    results = [r for m in conv.history for r in m.tool_results]
    assert len(results) == 1 and results[0].is_error and results[0].tool_use_id == "cancel-id"
    a.client = ScriptClient()
    conv.add_user_message("continue")
    await drive(a, conv)
    assert_closed(conv)


@pytest.mark.asyncio
async def test_completed_result_survives_stream_cancel(tmp_path):
    probe = Probe()
    hold = asyncio.Event()
    a = agent(tmp_path, ScriptClient([ToolCallComplete("done", "Probe", {})], hold), registry_for(probe))
    conv = ConversationManager()
    task = asyncio.create_task(drive(a, conv))
    for _ in range(100):
        if probe.count:
            break
        await asyncio.sleep(0.001)
    assert probe.count == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_closed(conv)
    result = next(r for m in conv.history for r in m.tool_results)
    assert not result.is_error and result.content == "executed"


@pytest.mark.asyncio
async def test_cancel_bash_terminates_child_process(tmp_path):
    ready = tmp_path / "ready"
    code = f"import os,time,pathlib; pathlib.Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)"
    task = asyncio.create_task(Bash().execute(BashParams(command=f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}")))
    try:
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        assert ready.exists()
        pid = int(ready.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_safe_reads_overlap_and_writes_form_barriers(tmp_path):
    log = []
    readers = 0
    both_reading = asyncio.Event()

    class ReadProbe(Probe):
        name, category, is_concurrency_safe = "ReadProbe", "read", True
        async def execute(self, params):
            nonlocal readers
            readers += 1
            log.append("read-start")
            if readers == 2:
                both_reading.set()
            if readers <= 2:
                await asyncio.wait_for(both_reading.wait(), 1)
            log.append("read-end")
            return ToolResult("read")

    class WriteProbe(Probe):
        name = "WriteProbe"
        async def execute(self, params):
            assert log.count("read-end") == 2
            log.append("write")
            return ToolResult("write")

    registry = registry_for(ReadProbe())
    registry.register(WriteProbe())
    names = ["ReadProbe", "ReadProbe", "WriteProbe", "ReadProbe"]
    await drive(agent(tmp_path, ScriptClient([ToolCallComplete(str(i), n, {}) for i, n in enumerate(names)]), registry))
    assert log[:2] == ["read-start", "read-start"]
    assert log.index("write") < len(log) - 2


@pytest.mark.asyncio
async def test_agent_versions_cannot_authorize_each_others_stale_writes(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("v1")
    registry = create_default_registry()
    a, b = agent(tmp_path, registry=registry), agent(tmp_path, registry=registry)
    read = ToolCallComplete("r", "ReadFile", {"file_path": "file.txt"})
    assert not (await a._execute_tool_noninteractive(read)).is_error
    assert not (await b._execute_tool_noninteractive(read)).is_error
    assert not (await b._execute_tool_noninteractive(ToolCallComplete("b", "WriteFile", {"file_path": "file.txt", "content": "B"}))).is_error
    result = await a._execute_tool_noninteractive(ToolCallComplete("a", "WriteFile", {"file_path": "file.txt", "content": "stale A"}))
    assert result.is_error and path.read_text() == "B"


@pytest.mark.asyncio
async def test_two_writes_from_one_model_response_cannot_share_old_version(tmp_path):
    (tmp_path / "file.txt").write_text("v1")
    a = agent(tmp_path, registry=create_default_registry())
    await a._execute_tool_noninteractive(ToolCallComplete("r", "ReadFile", {"file_path": "file.txt"}))
    a.client = ScriptClient([ToolCallComplete(str(i), "WriteFile", {"file_path": "file.txt", "content": str(i)}) for i in (1, 2)])
    conv, _ = await drive(a)
    results = [r for m in conv.history for r in m.tool_results]
    assert [r.is_error for r in results] == [False, True]
    assert (tmp_path / "file.txt").read_text() == "1"


@pytest.mark.asyncio
async def test_cache_populates_and_observes_external_changes(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("old")
    cache = FileCache()
    tool = ReadFile(cache)
    await tool.execute(ReadParams(file_path=str(path)))
    assert len(cache) == 1
    path.write_text("new")
    assert "new" in (await tool.execute(ReadParams(file_path=str(path)))).output


@pytest.mark.asyncio
async def test_shared_tools_use_each_agents_workspace_and_restore_context(tmp_path):
    registry = create_default_registry()
    agents = []
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        (root / "file.txt").write_text(name)
        agents.append(agent(root, registry=registry, sandbox_root=str(root)))
    results = await asyncio.gather(*(a._execute_tool_noninteractive(ToolCallComplete("r", "ReadFile", {"file_path": "file.txt"})) for a in agents))
    assert [r.output for r in results] == ["1\tone", "1\ttwo"]
    results = await asyncio.gather(*(a._execute_tool_noninteractive(ToolCallComplete("p", "Bash", {"command": "pwd"})) for a in agents))
    assert [Path(r.output.strip()).resolve() for r in results] == [Path(a.work_dir).resolve() for a in agents]
    assert current_runtime() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [True, False])
async def test_isolated_paths_reject_escape_even_in_bypass(tmp_path, relative):
    child = tmp_path / "child"
    child.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    a = agent(child, registry=create_default_registry(), sandbox_root=str(child),
              permission_checker=checker(child, PermissionMode.BYPASS))
    path = "../outside.txt" if relative else str(outside)
    result = await a._execute_tool_noninteractive(ToolCallComplete("r", "ReadFile", {"file_path": path}))
    assert result.is_error and "outside isolated workspace" in result.output
    (child / "link").symlink_to(outside)
    result = await a._execute_tool_noninteractive(ToolCallComplete("r", "ReadFile", {"file_path": "link"}))
    assert result.is_error


@pytest.mark.asyncio
async def test_provider_failure_after_tool_start_closes_history(tmp_path):
    started = asyncio.Event()
    owned = []
    probe = Probe()

    async def execute(params):
        owned.append(asyncio.current_task())
        started.set()
        await asyncio.Event().wait()

    probe.execute = execute

    class FailingClient(ScriptClient):
        async def stream(self, conversation, system="", tools=None):
            yield ToolCallComplete("id", "Probe", {})
            await started.wait()
            raise RuntimeError("stream disconnected")

    a = agent(tmp_path, FailingClient(), registry_for(probe))
    conv = ConversationManager()
    with pytest.raises(RuntimeError, match="stream disconnected"):
        await drive(a, conv)
    assert_closed(conv)
    assert all(task.done() for task in owned)


@pytest.mark.asyncio
async def test_repeated_invalid_arguments_stop_after_three_turns(tmp_path):
    class InvalidClient(ScriptClient):
        async def stream(self, conversation, system="", tools=None):
            self.round += 1
            yield ToolCallComplete(str(self.round), "Probe", {}, "{bad", "invalid JSON")
            yield StreamEnd("tool_use")

    client, probe = InvalidClient(), Probe()
    a = agent(tmp_path, client, registry_for(probe))
    conv, _ = await drive(a)
    assert client.round == 3 and probe.count == 0
    assert_closed(conv)


@pytest.mark.asyncio
async def test_cancel_while_approval_is_pending(tmp_path):
    probe = Probe()
    a = agent(tmp_path, ScriptClient([ToolCallComplete("ask", "Probe", {})]), registry_for(probe),
              permission_checker=checker(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("test")
    stream = a.run(conv)
    async for event in stream:
        if isinstance(event, PermissionRequest):
            request = event
            break
    await stream.aclose()
    assert request.future.cancelled() and probe.count == 0
    assert_closed(conv)


def test_dangerous_command_cannot_be_downgraded_to_ask(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("- rule: 'Bash(*)'\n  effect: ask\n")
    check = checker(tmp_path, rules=RuleEngine(project_rules_path=rules))
    assert check.check(Bash(), {"command": "rm -rf /"}).effect == "deny"


@pytest.mark.asyncio
async def test_callback_failure_closes_generator_before_returning(tmp_path):
    probe = Probe()
    a = agent(tmp_path, ScriptClient([ToolCallComplete("id", "Probe", {})]), registry_for(probe))
    conv = ConversationManager()

    def callback(event):
        if event["type"] == "tool_use":
            raise RuntimeError("consumer failed")

    with pytest.raises(RuntimeError, match="consumer failed"):
        await a.run_to_completion("task", conv, event_callback=callback)
    assert_closed(conv)
    assert a._pending_tool_turn is None


@pytest.mark.asyncio
async def test_error_inside_tool_triggers_post_hook_once(tmp_path):
    probe = Probe()
    probe.execute = AsyncMock(side_effect=RuntimeError("tool failed"))
    engine = HookEngine()
    engine.run_hooks = AsyncMock()
    a = agent(tmp_path, registry=registry_for(probe), hook_engine=engine)
    result = await a._execute_tool_noninteractive(ToolCallComplete("id", "Probe", {}))
    assert result.is_error
    post = [call for call in engine.run_hooks.await_args_list if call.args[0] == "post_tool_use"]
    assert len(post) == 1 and "tool failed" in post[0].args[1].error
