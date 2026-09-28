"""Exercise session ownership through the real Textual command/run paths."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from nanocursor.agent import LoopComplete, StreamText
from nanocursor.app import ChatInput
from nanocursor.conversation import Message
from test_approval_ui import make_app
from test_auto_approval import setup
from test_status_ui import show_chat


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['/session new', '/clear', '/session resume missing', '/compact'])
async def test_streaming_session_mutations_preserve_reply_and_input(setup, monkeypatch, command):
    app = make_app(setup)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.session
    async def run(conv, **kwargs):
        entered.set()
        yield StreamText('partial')
        await release.wait()
        conv.add_assistant_message('owned reply')
        yield LoopComplete(1)
    monkeypatch.setattr(setup.agent, 'run', run)
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command('hello')
        await asyncio.wait_for(entered.wait(), 2)
        await app._dispatch_command(command)
        assert app.session is original
        assert app.query_one(ChatInput).text == command
        release.set()
        await app._agent_task
        recovered = app.session_manager.resume(original.session_id)
        assert any(m.content == 'owned reply' for m in recovered.messages)
        recovered.session.close()


@pytest.mark.asyncio
async def test_waiting_mcp_is_busy_and_cancel_does_not_cancel_shared_connection(setup):
    app = make_app(setup)
    mcp_ready = asyncio.Event()
    app._mcp_init_task = asyncio.create_task(mcp_ready.wait())
    async with app.run_test() as pilot:
        show_chat(app)
        original = app.session
        await app._dispatch_command('hello')
        await asyncio.sleep(0)
        assert not app._streaming
        await app._dispatch_command('/session new')
        assert app.session is original
        assert await app._cancel_foreground()
        assert not app._mcp_init_task.done()
        assert not app.foreground_busy()
        mcp_ready.set()
        await app._mcp_init_task
        await app._dispatch_command('/session new')
        assert app.session is not original


@pytest.mark.asyncio
async def test_resume_number_is_last_shown_snapshot(setup):
    app = make_app(setup)
    target = app.session_manager.create()
    target.append(Message('user', 'restore this'))
    target.close()
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command('/session resume')
        candidates = app._resume_candidates
        idx = candidates.index(target.session_id) + 1
        unrelated = app.session_manager.create()
        unrelated.close()
        await app._dispatch_command(f'/session resume {idx}')
        assert app.session.session_id == target.session_id
        assert [m.content for m in app.conversation.history] == ['restore this']
        assert not app._resume_candidates


@pytest.mark.asyncio
async def test_late_summary_cannot_update_new_session(setup, monkeypatch):
    app = make_app(setup)
    app.client = setup.agent.client
    entered, release = asyncio.Event(), asyncio.Event()
    async def summary(*args):
        entered.set()
        await release.wait()
        return 'old summary'
    monkeypatch.setattr('nanocursor.app.generate_session_summary', summary)
    async with app.run_test() as pilot:
        show_chat(app)
        app._schedule_session_summary(app.session, app.conversation, app.agent)
        await entered.wait()
        await app._dispatch_command('/session new')
        new = app.session
        release.set()
        await asyncio.sleep(0)
        assert new.meta.summary == ''
        assert not app._owned_tasks


@pytest.mark.asyncio
async def test_compact_uses_restored_context_not_lifetime_tokens(setup, monkeypatch):
    app = make_app(setup)
    app.conversation.history = [Message('user', 'long context ' * 6000)]
    compact = AsyncMock(return_value=None)
    monkeypatch.setattr(app.agent, 'manual_compact', compact)
    assert app.agent.total_input_tokens == 0
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command('/compact')
        task = app._command_task
        assert task is not None
        await task
        compact.assert_awaited_once_with(app.conversation)


@pytest.mark.asyncio
async def test_cancel_waits_for_tool_cleanup_and_persists_paired_results(setup, monkeypatch):
    from nanocursor.permissions import PermissionMode
    from nanocursor.tools.base import StreamEnd, ToolCallComplete
    from test_permissions import MockLLMClient
    app = make_app(setup)
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def execute(params):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()
    monkeypatch.setattr(setup.bash, 'execute', execute)
    setup.agent.set_permission_mode(PermissionMode.BYPASS)
    setup.agent.client = MockLLMClient([[ToolCallComplete('once', 'Bash', {'command': 'test'}), StreamEnd('tool_use')]])
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command('perform task')
        await asyncio.wait_for(entered.wait(), 2)
        first, second = await asyncio.gather(app._cancel_foreground(), app._cancel_foreground())
        assert first and second and cleaned.is_set()
        restored = app.session_manager.resume(app.session.session_id)
        calls = [c for m in restored.messages for c in m.tool_uses]
        results = [r for m in restored.messages for r in m.tool_results]
        assert len(calls) == len(results) == 1
        assert results[0].tool_use_id == calls[0].tool_use_id
        restored.session.close()
        assert not app.foreground_busy()
        assert app._notifications_suspended


@pytest.mark.asyncio
async def test_pending_completion_saved_to_old_session_before_switch(setup):
    app = make_app(setup)
    app.task_manager.current_session_id = app.session.session_id
    task_id = app.task_manager.launch(setup.agent, 'background')
    await app.task_manager._async_tasks[task_id]
    old_id = app.session.session_id
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command('/session new')
        restored = app.session_manager.resume(old_id)
        assert any(task_id in message.content for message in restored.messages)
        assert not any(task_id in message.content for message in app.conversation.history)
        restored.session.close()
