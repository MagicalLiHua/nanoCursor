"""Memory controls through the actual mounted, provider-selected TUI."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from nanocursor.app import NanoCursorApp
from nanocursor.client import LLMClient
from nanocursor.config import ProviderConfig
from nanocursor.conversation import Message
from nanocursor.memory.consolidation import SYSTEM_PROMPT
from nanocursor.memory.session import SessionManager
from nanocursor.memory.store import active_records, render_memory
from nanocursor.tools.base import StreamEnd, TextDelta
from nanocursor.workspace import WorkspaceContext


class MemoryUIClient(LLMClient):
    max_output_tokens = 3000

    def __init__(self):
        self.consolidation_calls = []
        self.main_history = []
        self.entered = asyncio.Event()
        self.block = False
        self.cancelled = asyncio.Event()

    async def stream(self, conversation, system="", tools=None, **kwargs):
        if system == SYSTEM_PROMPT:
            payload = json.loads(conversation.history[0].content)
            self.consolidation_calls.append(payload)
            self.entered.set()
            if self.block:
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
            sources = [record["id"] for record in payload["memories"]
                       if record["filename"] in {"alpha.md", "beta.md"}]
            yield TextDelta(json.dumps({"schema_version": 1, "noop": False, "groups": [{
                "sources": sources, "name": "Combined memory", "description": "Use Python 3.11",
                "type": "project", "body": "This project uses Python 3.11."}]}))
        elif system == "You are a memory extraction assistant.":
            yield TextDelta("NONE")
        elif "selecting memories" in system:
            yield TextDelta('{"selected_memories": []}')
        elif "SESSION" in system or "总结" in system:
            yield TextDelta("A short session summary")
        else:
            self.main_history.append(deepcopy(conversation.history))
            yield TextDelta("完成。")
        yield StreamEnd("end_turn", input_tokens=100, output_tokens=15)


@pytest.fixture
def memory_app(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    root = project / ".nanocursor" / "memory"
    root.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (root / f"{name}.md").write_text(render_memory(name, "Python 3.11", "project", "Use Python 3.11"))
    (root / "MEMORY.md").write_text("- [Alpha](alpha.md) — Python\n- [Beta](beta.md) — Python\n")
    manager = SessionManager(str(project))
    for number in range(5):
        session = manager.create()
        session.append(Message("user", f"Remember project fact {number}"))
        session.close()
    client = MemoryUIClient()
    monkeypatch.setattr("nanocursor.app.create_client", lambda _: client)
    provider = ProviderConfig("offline", "openai-compat", "https://invalid.example/v1", "offline", auth="none",
                              context_window=32_000, max_output_tokens=3000)
    def make(enabled=False):
        app = NanoCursorApp([provider], memory_consolidation_enabled=enabled,
                            workspace=WorkspaceContext.resolve(str(project)))
        return app
    return make, client, root


async def command(app, text):
    await app._dispatch_command(text)
    task = app._command_task
    if task is not None:
        await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_selected_provider_default_off_status_and_explicit_on_publish(memory_app):
    make, client, root = memory_app
    app = make()
    async with app.run_test(size=(90, 30)) as pilot:
        assert app.agent and app._consolidator
        assert not app._consolidator.enabled
        assert app._consolidator._context_window == 32_000
        app._schedule_consolidation()
        await command(app, "/memory consolidate status")
        await pilot.pause()
        assert not client.consolidation_calls and app._consolidation_task is None
        await command(app, "/memory consolidate on")
        assert app._consolidation_task is not None
        await asyncio.wait_for(app._consolidation_task, 3)
        await pilot.pause()
        assert len(client.consolidation_calls) == 1
        assert len(active_records(root)) == 1
        assert app._consolidator.status["committed_scopes"] == ["project"]
        assert app._memory_refresh_pending
        await command(app, "/memory consolidate off")
        assert not app._consolidator.enabled
        app._schedule_consolidation()
        assert len(client.consolidation_calls) == 1
        assert len(active_records(root)) == 1


@pytest.mark.asyncio
async def test_off_waits_for_actual_background_request(memory_app):
    make, client, root = memory_app
    client.block = True
    app = make()
    async with app.run_test(size=(80, 24)) as pilot:
        await command(app, "/memory consolidate on")
        await asyncio.wait_for(client.entered.wait(), 3)
        task = app._consolidation_task
        assert task in app._owned_tasks
        await command(app, "/memory consolidate off")
        assert client.cancelled.is_set() and task.done()
        assert app._consolidator._task is None
        assert len(active_records(root)) == 2


@pytest.mark.asyncio
async def test_exit_cancels_consolidation_and_leaves_no_owned_tasks(memory_app):
    make, client, root = memory_app
    client.block = True
    app = make(enabled=True)
    async with app.run_test(size=(50, 20)) as pilot:
        app._schedule_consolidation()
        await asyncio.wait_for(client.entered.wait(), 3)
        task = app._consolidation_task
        assert await app._shutdown_runtime()
        assert client.cancelled.is_set() and task.done()
        assert app._consolidator._task is None
        assert not [task for task in app._owned_tasks if not task.done()]
        assert len(active_records(root)) == 2
        assert await app._shutdown_runtime()  # Repeated shutdown remains safe.
        app._schedule_consolidation()
        assert app._consolidation_task is task
        assert task.done() and len(client.consolidation_calls) == 1


@pytest.mark.asyncio
async def test_completed_consolidation_refreshes_next_actual_foreground_request(memory_app):
    make, client, root = memory_app
    app = make()
    async with app.run_test(size=(120, 32)) as pilot:
        await command(app, "First task")
        await asyncio.wait_for(app._agent_task, 3)
        assert any("](alpha.md)" in message.content for message in client.main_history[0])
        await command(app, "/memory consolidate on")
        await asyncio.wait_for(app._consolidation_task, 3)
        await pilot.pause()
        assert app._memory_refresh_pending
        client.main_history.clear()
        await command(app, "Second task")
        await asyncio.wait_for(app._agent_task, 3)
        refreshes = [message.content for message in client.main_history[0]
                     if message.memory_context and message.memory_context.get("kind") == "index"]
        assert len(refreshes) == 1
        assert "consolidated-" in refreshes[0]
        assert "](alpha.md)" not in refreshes[0] and "](beta.md)" not in refreshes[0]
        assert not app._memory_refresh_pending
        # The next idle scan is throttled. It must not turn the old commit into
        # a fresh publication or append another refresh on a later user turn.
        revision = app._consolidator.publication_revision
        await asyncio.wait_for(app._consolidation_task, 3)
        await pilot.pause()
        assert app._consolidator.publication_revision == revision
        assert not app._memory_refresh_pending
        client.main_history.clear()
        await command(app, "Third task")
        await asyncio.wait_for(app._agent_task, 3)
        await asyncio.wait_for(app._consolidation_task, 3)
        await pilot.pause()
        refreshes = [message.content for message in client.main_history[0]
                     if message.memory_context and message.memory_context.get("kind") == "index"]
        assert len(refreshes) == 1
        assert not app._memory_refresh_pending


@pytest.mark.asyncio
async def test_stopping_does_not_schedule_consolidation(memory_app):
    make, client, root = memory_app
    app = make(enabled=True)
    async with app.run_test(size=(80, 24)) as pilot:
        app._stopping = True
        app._schedule_consolidation()
        await asyncio.sleep(0)
        assert app._consolidation_task is None
        assert not client.consolidation_calls
        assert not [task for task in app._owned_tasks if not task.done()]
        app._stopping = False


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(90, 30), (80, 24)])
async def test_pure_text_first_response_recall_off_clear_and_resume(memory_app, size):
    from nanocursor.memory.context import owned
    make, client, root = memory_app
    app = make()
    async with app.run_test(size=size) as pilot:
        await command(app, "Python 版本")
        await asyncio.wait_for(app._agent_task, 3)
        recalled = [m for m in client.main_history[0] if owned(m) and m.memory_context["kind"] == "recall"]
        assert recalled and all("Python 3.11" in m.content for m in recalled)
        assert app.agent.memory_recall.requests == 0
        assert app.agent.memory_recall.injected >= 2
        await command(app, "/memory recall off")
        assert not [m for m in app.conversation.history if owned(m) and m.memory_context["kind"] == "recall"]
        assert [m for m in app.conversation.history if owned(m) and m.memory_context["kind"] == "index"]
        await command(app, "/memory recall local")
        (root / "alpha.md").write_text(render_memory("alpha", "Python version", "project", "Python UPDATED_BODY"))
        await command(app, "Python version")
        await asyncio.wait_for(app._agent_task, 3)
        assert any("UPDATED_BODY" in m.content for m in app.conversation.history if owned(m))
        session_id = app.session.session_id
        await command(app, "/memory clear")
        assert not [m for m in app.conversation.history if owned(m)]
        restored = app.session_manager.resume(session_id)
        assert not [m for m in restored.messages if owned(m)]
        restored.session.close()
        assert app.agent.memory_recall.context_tokens == 0
        assert app.size.width == size[0]
        await command(app, "Python version")
        await app._agent_task
        assert app.agent.memory_recall.context_tokens > 0
        await command(app, "/clear")
        assert app.agent.memory_recall.context_tokens == 0


@pytest.mark.asyncio
async def test_recall_boundary_write_failure_stops_before_main_request(memory_app, monkeypatch):
    from nanocursor.memory.session import RecordType
    from nanocursor.memory.context import owned
    make, client, _ = memory_app
    app = make()
    async with app.run_test() as pilot:
        original = app.session.append_record
        def fail(record):
            if record.type == RecordType.HISTORY_BOUNDARY:
                raise OSError("injected disk failure")
            return original(record)
        monkeypatch.setattr(app.session, "append_record", fail)
        await command(app, "Python version")
        with pytest.raises(OSError, match="injected disk failure"):
            await app._agent_task
        assert not client.main_history
        assert not [m for m in app.conversation.history if owned(m)]


@pytest.mark.asyncio
async def test_recall_metadata_failure_keeps_committed_memory(memory_app, monkeypatch):
    from nanocursor.memory.context import owned
    from nanocursor.memory.session import RecordType
    from test_committed_session_metadata import fail_boundary_metadata_once
    make, client, _ = memory_app
    app = make()
    async with app.run_test() as pilot:
        state, _ = fail_boundary_metadata_once(app, monkeypatch, RecordType.HISTORY_BOUNDARY)
        await command(app, "Python version")
        await app._agent_task
        assert state["failed"] and client.main_history
        expected = [m.memory_context for m in app.conversation.history if owned(m)]
        resumed = app.session_manager.resume(app.session.session_id)
        assert [m.memory_context for m in resumed.messages if owned(m)] == expected
        resumed.session.close()


@pytest.mark.asyncio
async def test_rewind_then_refresh_and_resume_preserves_memory_sources(memory_app):
    from nanocursor.memory.context import owned
    make, client, root = memory_app
    app = make()
    async with app.run_test() as pilot:
        await command(app, "Python version")
        await app._agent_task
        checkpoint = deepcopy(app.conversation.history)
        snapshot = app.agent.file_history.begin_checkpoint(len(checkpoint), "memory checkpoint", conversation=checkpoint)
        (root / "alpha.md").write_text(render_memory("alpha", "Python", "project", "Python NEW_VERSION"))
        await command(app, "Python version")
        await app._agent_task
        await command(app, f"/rewind {snapshot.checkpoint_id} 2 apply")
        assert [m.memory_context for m in app.conversation.history if owned(m)] == [m.memory_context for m in checkpoint if owned(m)]
        await command(app, "Python version")
        await app._agent_task
        assert any("NEW_VERSION" in m.content for m in app.conversation.history if owned(m))
        resumed = app.session_manager.resume(app.session.session_id)
        assert [m.memory_context for m in resumed.messages if owned(m)] == [m.memory_context for m in app.conversation.history if owned(m)]
        resumed.session.close()
