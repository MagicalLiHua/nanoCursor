"""Exercise real Agent dispatch and persisted recovery across process loss."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from nanocursor.agent import Agent, ErrorEvent, ToolResultEvent
from nanocursor.client import LLMClient
from nanocursor.conversation import ConversationManager
from nanocursor.hooks.engine import HookEngine
from nanocursor.hooks.models import Action, ActionResult, Hook, HookContext
from nanocursor.memory.session import SessionManager
from nanocursor.recovery import RecoveryRuntime, RecoveryStore, RecoveryRequired, RecoveryStorageError
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import Tool, ToolResult, ToolCallComplete, StreamEnd, TextDelta


class Params(BaseModel):
    pass


class Marker(Tool):
    name, description, category = 'Marker', 'Write a marker for a local test', 'command'
    params_model = Params

    def __init__(self, path, runtime, *, crash=False):
        self.path, self.runtime, self.crash = path, runtime, crash

    async def execute(self, params):
        records = self.runtime.store.projection_records(self.runtime.session_key)
        assert any(r['type'] == 'assistant' for r in records)
        self.path.write_text(self.path.read_text() + 'x' if self.path.exists() else 'x')
        if self.crash:
            os._exit(72)
        return ToolResult('observed once')


class Script(LLMClient):
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = 0

    async def stream(self, *args, **kwargs):
        self.requests += 1
        for event in next(self.turns, [TextDelta('done'), StreamEnd('end_turn')]):
            if isinstance(event, Exception):
                raise event
            yield event


@pytest.fixture
def host(tmp_path, monkeypatch):
    work = tmp_path / 'project'
    work.mkdir()
    monkeypatch.setenv('NANOCURSOR_HOME', str(tmp_path / 'home'))
    store = RecoveryStore(tmp_path / 'state')
    runtime = RecoveryRuntime.acquire(work, store=store)
    manager = SessionManager(str(work), recovery=runtime)
    session = manager.create()
    yield runtime, manager, session, work
    if not session._file.closed:
        session.close()
    runtime.close()
    store.close()


def make_agent(host, client, *, hook_engine=None, tool=None):
    runtime, _, session, work = host
    registry = ToolRegistry()
    registry.register(tool or Marker(work / 'marker', runtime))
    a = Agent(client, registry, 'anthropic', str(work), recovery=runtime,
              hook_engine=hook_engine, inject_environment_context=False)
    a.session_id = session.session_id
    return a


async def drive(a):
    conversation = ConversationManager()
    conversation.add_user_message('one operation')
    events = [event async for event in a.run(conversation)]
    return conversation, events


@pytest.mark.asyncio
async def test_partial_model_stream_never_dispatches_and_has_no_unknown(host):
    client = Script([[ToolCallComplete('a', 'Marker', {}), RuntimeError('stream lost')]])
    with pytest.raises(RuntimeError, match='stream lost'):
        await drive(make_agent(host, client))
    runtime, manager, session, work = host
    assert not (work / 'marker').exists()
    assert runtime.pending() == []
    assert not runtime.store.rows("SELECT * FROM operations WHERE kind='tool'")
    resumed = manager.resume(session.session_id)
    assert all(not message.tool_uses for message in resumed.messages)
    resumed.session.close()


@pytest.mark.asyncio
async def test_intent_commit_failure_stops_before_effect(host, monkeypatch):
    runtime, _, _, work = host
    monkeypatch.setattr(runtime, 'start_operation', lambda _: (_ for _ in ()).throw(RecoveryStorageError('disk full')))
    client = Script([[ToolCallComplete('a', 'Marker', {}), StreamEnd('end_turn')]])
    _, events = await drive(make_agent(host, client))
    assert not (work / 'marker').exists()
    assert client.requests == 1
    assert any(isinstance(event, ErrorEvent) and event.code == 'recovery_storage_error' for event in events)


@pytest.mark.asyncio
async def test_post_hook_unknown_preserves_known_tool_result_and_stops_next_model(host, monkeypatch):
    runtime, manager, session, work = host
    hook = Hook(id='post', event='post_tool_use', action=Action(type='http', url='http://unused.invalid'))
    monkeypatch.setattr('nanocursor.hooks.engine.execute_action', AsyncMock(return_value=
                        ActionResult('response lost after external commit', False, outcome_unknown=True)))
    client = Script([[ToolCallComplete('a', 'Marker', {}), StreamEnd('end_turn')]])
    _, events = await drive(make_agent(host, client, hook_engine=HookEngine([hook])))
    assert client.requests == 1
    assert (work / 'marker').read_text() == 'x'
    assert any(isinstance(e, ErrorEvent) and e.code == 'recovery_required' for e in events)
    assert [r['kind'] for r in runtime.pending()] == ['hook']
    resumed = manager.resume(session.session_id)
    result = next(r for msg in resumed.messages for r in msg.tool_results)
    assert result.content == 'observed once' and not result.is_error
    resumed.session.close()


@pytest.mark.asyncio
async def test_post_hook_exception_preserves_known_result_during_generator_cleanup(host, monkeypatch):
    runtime, _, _, work = host
    hook = Hook(id='post', event='post_tool_use', action=Action(type='http', url='http://unused.invalid'))
    monkeypatch.setattr('nanocursor.hooks.engine.execute_action', AsyncMock(side_effect=RecoveryRequired(message='hook stopped')))
    conversation, events = await drive(make_agent(host, Script([[ToolCallComplete('a', 'Marker', {}), StreamEnd('end_turn')]]), hook_engine=HookEngine([hook])))
    assert (work / 'marker').read_text() == 'x'
    result = next(r for message in conversation.history for r in message.tool_results)
    assert result.content == 'observed once' and not result.is_error
    assert any(isinstance(e, ErrorEvent) for e in events)


@pytest.mark.asyncio
async def test_transport_unknown_blocks_dependent_tools_and_never_retries(host):
    runtime, _, _, work = host
    class Uncertain(Marker):
        async def execute(self, params):
            self.path.write_text('x')
            return ToolResult('MCP response unavailable', True, outcome_unknown=True)
    client = Script([[ToolCallComplete('a', 'Marker', {}), ToolCallComplete('b', 'Marker', {}), StreamEnd('end_turn')]])
    _, events = await drive(make_agent(host, client, tool=Uncertain(work / 'marker', runtime)))
    assert client.requests == 1
    assert (work / 'marker').read_text() == 'x'
    assert len(runtime.pending()) == 1
    assert any(isinstance(e, ErrorEvent) for e in events)
    assert runtime.store.rows("SELECT state FROM operations WHERE tool_call_id='b'")[0]['state'] == 'interrupted'


CRASH_PROGRAM = r'''
import asyncio,os,sys
from pathlib import Path
from nanocursor.agent import Agent
from nanocursor.client import LLMClient
from nanocursor.conversation import ConversationManager
from nanocursor.memory.session import SessionManager
from nanocursor.recovery import RecoveryStore,RecoveryRuntime
from nanocursor.tools import ToolRegistry
from nanocursor.tools.base import Tool,ToolResult,ToolCallComplete,StreamEnd
from pydantic import BaseModel
work,state,phase=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
r=RecoveryRuntime.acquire(work,store=RecoveryStore(state))
s=SessionManager(str(work),recovery=r).create()
(work/'session-id').write_text(s.session_id)
class P(BaseModel): pass
class T(Tool):
 name,description,category='Effect','test','command'
 params_model=P
 async def execute(self,p):
  path=work/'counter'
  path.write_text(path.read_text()+'x' if path.exists() else 'x')
  if phase=='after_effect': os.kill(os.getpid(),9)
  return ToolResult('old observed value')
class C(LLMClient):
 async def stream(self,*a,**k):
  yield ToolCallComplete('id','Effect',{})
  yield StreamEnd('end_turn')
def fault(at):
 if at=={'before_effect':'operation_intent_committed','after_result':'operation_result_committed'}.get(phase):
  os.kill(os.getpid(),9)
r.store.fault_hook=fault
registry=ToolRegistry(); registry.register(T())
a=Agent(C(),registry,'anthropic',str(work),recovery=r,inject_environment_context=False)
a.session_id=s.session_id
conv=ConversationManager();conv.add_user_message('run once')
async def run():
 async for _ in a.run(conv): pass
asyncio.run(run())
'''


@pytest.mark.parametrize('phase,executions,unknown', [
    ('before_effect', 0, True), ('after_effect', 1, True), ('after_result', 1, False),
])
def test_sigkill_at_real_agent_commit_boundaries(tmp_path, phase, executions, unknown):
    work, state = tmp_path / 'project', tmp_path / 'state'
    work.mkdir()
    completed = subprocess.run([sys.executable, '-c', CRASH_PROGRAM, str(work), str(state), phase],
                               timeout=15, capture_output=True, text=True)
    assert completed.returncode == -9, completed.stderr
    marker = work / 'counter'
    assert (len(marker.read_text()) if marker.exists() else 0) == executions
    session_id = (work / 'session-id').read_text()
    # Change the world after the process died. Recovery must use its observation.
    if phase == 'after_result':
        (work / 'current-value').write_text('a newer external value')
    for _ in range(2):
        store = RecoveryStore(state)
        runtime = RecoveryRuntime.acquire(work, store=store)
        manager = SessionManager(str(work), recovery=runtime)
        resumed = manager.resume(session_id)
        try:
            assert bool(runtime.pending()) == unknown
            results = [r for m in resumed.messages for r in m.tool_results]
            assert len(results) == 1
            if not unknown:
                assert results[0].content == 'old observed value'
                row = store.rows("SELECT observed_at FROM operations WHERE kind='tool'")[0]
                assert row['observed_at']
            else:
                assert 'unknown' in results[0].content
            assert (len(marker.read_text()) if marker.exists() else 0) == executions
        finally:
            resumed.session.close()
            runtime.close()
            store.close()


@pytest.mark.asyncio
async def test_prompt_recovery_gate_has_machine_status_before_client_creation(tmp_path, monkeypatch, capsys):
    from nanocursor.__main__ import _run_prompt
    from nanocursor.permissions import PermissionMode
    from nanocursor.workspace import WorkspaceContext
    monkeypatch.setenv('NANOCURSOR_HOME', str(tmp_path / 'home'))
    work = tmp_path / 'project'
    work.mkdir()
    runtime = RecoveryRuntime.acquire(work)
    operation = runtime.begin_operation('tool', 'database.update')
    runtime.mark_unknown(operation, 'response lost')
    runtime.close()
    monkeypatch.setattr('nanocursor.client.create_client', lambda _: pytest.fail('model must not be created'))
    code = await _run_prompt(None, PermissionMode.DEFAULT, None, 'new task', 'stream-json', workspace=WorkspaceContext.resolve(work))
    assert code == 3
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports[-1]['type'] == 'result' and reports[-1]['stop_reason'] == 'recovery_required'


def test_recovery_cli_can_inspect_live_owner_without_credentials(tmp_path, monkeypatch, capsys):
    from nanocursor.__main__ import main
    monkeypatch.setenv('NANOCURSOR_HOME', str(tmp_path / 'home'))
    work = tmp_path / 'project'
    work.mkdir()
    runtime = RecoveryRuntime.acquire(work)
    operation = runtime.begin_operation('tool', 'Bash', {'command': 'historical example'})
    try:
        monkeypatch.setattr(sys, 'argv', ['nanocursor', 'recover', '--cwd', str(work), '--list', '--json'])
        monkeypatch.setattr('nanocursor.__main__.load_config', lambda **_: pytest.fail('must not need credentials'))
        main()
        report = json.loads(capsys.readouterr().out)
        assert report['read_only'] and report['owner_active']
        assert report['unknown_operations'] == []
        assert report['live_operations'][0]['operation_id'] == operation
        assert runtime.store.rows('SELECT state FROM operations')[0]['state'] == 'intent'
    finally:
        runtime.finish_operation(operation, 'done')
        runtime.close()


@pytest.mark.asyncio
async def test_notifications_do_not_create_new_user_checkpoints(host):
    a = make_agent(host, Script([[TextDelta('notification read'), StreamEnd('end_turn')]]))
    conversation = ConversationManager()
    conversation.add_user_message('<system-reminder>Background result</system-reminder>')
    _ = [event async for event in a.run(conversation, source='notification')]
    runtime = host[0]
    assert runtime.store.rows('SELECT source FROM runs')[0]['source'] == 'notification'
    assert a.file_history.get_snapshots() == []


@pytest.mark.asyncio
async def test_background_hook_cancelled_before_start_has_no_unknown(host):
    runtime, _, _, work = host
    engine = HookEngine([Hook(id='unstarted', event='shutdown', async_exec=True,
                             action=Action(type='command', command='echo must-not-run'))])
    with runtime.activate():
        await engine.run_hooks('shutdown', HookContext(event_name='shutdown'))
        assert await engine.shutdown()
    row = runtime.store.rows("SELECT * FROM operations WHERE kind='hook'")[0]
    assert row['state'] == 'interrupted'
    assert 'before' in row['observation']
    assert runtime.pending() == []
    assert runtime.store.list_metadata('process') == []


@pytest.mark.asyncio
async def test_http_hook_cancel_after_server_commit_stays_unknown_and_does_not_retry(host):
    runtime = host[0]
    received, release = threading.Event(), threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)  # Represents a remote committed operation.
            received.set()
            release.wait(5)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    engine = HookEngine([Hook(id='committed-http', event='turn_end', action=Action(
        type='http', url=f'http://127.0.0.1:{server.server_port}/commit'))])
    task = None
    try:
        with runtime.activate():
            task = asyncio.create_task(engine.run_hooks('turn_end', HookContext(event_name='turn_end')))
            assert await asyncio.to_thread(received.wait, 5), 'HTTP request did not reach local server'
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(runtime.pending()) == 1
        assert runtime.pending()[0]['name'] == 'committed-http'
        with pytest.raises(RecoveryRequired):
            runtime.ensure_ready()
        release.set()
        await asyncio.to_thread(server.shutdown)
        assert requests == ['/commit']
        assert runtime.pending()[0]['state'] == 'outcome_unknown'
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
