"""Failure and cancellation edges through real App lifecycle methods."""
import asyncio

import pytest

from nanocursor.app import ChatInput
from nanocursor.memory.session import Session
from test_approval_ui import make_app
from test_auto_approval import setup
from test_status_ui import show_chat


@pytest.mark.asyncio
async def test_completed_background_result_can_retry_after_persistence_failure(setup, monkeypatch):
    app = make_app(setup)
    app.task_manager.current_session_id = app.session.session_id
    task_id = app.task_manager.launch(setup.agent, "background")
    await app.task_manager._async_tasks[task_id]
    await asyncio.sleep(0)
    original_append = Session.append
    failures = 0

    def fail_once(self, message):
        nonlocal failures
        if self is app.session and task_id in message.content and failures == 0:
            failures += 1
            raise OSError("disk temporarily unavailable")
        return original_append(self, message)

    async with app.run_test() as pilot:
        show_chat(app)
        monkeypatch.setattr(Session, "append", fail_once)
        with pytest.raises(OSError, match="temporarily unavailable"):
            await app._process_task_notifications(start_run=False)
        await app._process_task_notifications(start_run=False)
        restored = app.session_manager.resume(app.session.session_id)
        try:
            persisted = [message for message in restored.messages if task_id in message.content]
            assert len(persisted) == 1
            assert len([message for message in app.conversation.history if task_id in message.content]) == 1
        finally:
            restored.session.close()


@pytest.mark.asyncio
async def test_cancel_before_user_row_mount_also_cancels_its_recall(setup, monkeypatch):
    app = make_app(setup)
    setup.agent.memory_manager = object()
    recall_entered, recall_released, recall_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    mount_entered, mount_released = asyncio.Event(), asyncio.Event()

    async def recall(query):
        recall_entered.set()
        try:
            await recall_released.wait()
            return "old recall"
        finally:
            recall_finished.set()

    monkeypatch.setattr(app, "_prefetch_relevant_memories", recall)
    async with app.run_test() as pilot:
        show_chat(app)
        chat = app.query_one("#chat-area")
        original_mount = chat.mount

        def mount(*widgets, **kwargs):
            if any(widget.has_class("user-row") for widget in widgets):
                async def gated_mount():
                    mount_entered.set()
                    await mount_released.wait()
                    return await original_mount(*widgets, **kwargs)
                return gated_mount()
            return original_mount(*widgets, **kwargs)

        monkeypatch.setattr(chat, "mount", mount)
        try:
            await app._dispatch_command("cancel during initial UI work")
            await asyncio.wait_for(mount_entered.wait(), 2)
            await asyncio.wait_for(recall_entered.wait(), 2)
            assert await app._cancel_foreground()
            assert recall_finished.is_set()
            assert not any(not task.done() and kind == "recall" for task, (_, kind) in app._owned_tasks.items())
        finally:
            mount_released.set()
            recall_released.set()
            await app._cancel_owned_tasks()


@pytest.mark.asyncio
async def test_shutdown_does_not_wait_forever_for_cancel_resistant_optional_job(setup, monkeypatch):
    app = make_app(setup)
    setup.agent.memory_manager = object()
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def extraction(conversation):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    monkeypatch.setattr(setup.agent, "_extract_memories", extraction)
    async with app.run_test() as pilot:
        show_chat(app)
        shutdown = asyncio.create_task(app._shutdown_runtime())
        try:
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.wait_for(cancelled.wait(), 4)
            # One bounded cancellation grace period may follow the initial
            # optional-work deadline. The app must report that it is still
            # closing, retain the job, and let shutdown be retried.
            done, _ = await asyncio.wait({shutdown}, timeout=6)
            assert done and shutdown.result() is False
            assert app._runtime_closing
            assert not app.session._file.closed
        finally:
            release.set()
            await asyncio.wait_for(shutdown, 2)
            await app._shutdown_runtime()


@pytest.mark.asyncio
async def test_automatic_compaction_persistence_failure_restores_owned_history(setup, monkeypatch):
    import copy

    from nanocursor.client import LLMClient
    from nanocursor.context.manager import SUMMARY_PROMPT
    from nanocursor.conversation import Message
    from nanocursor.memory.session import RecordType
    from nanocursor.tools.base import StreamEnd, TextDelta

    class Summarizer(LLMClient):
        async def stream(self, conversation, *, system="", **kwargs):
            assert system == SUMMARY_PROMPT, "Failed boundary must stop before an ordinary request"
            yield TextDelta("<summary>validated summary</summary>")
            yield StreamEnd("end_turn")

    app = make_app(setup)
    app.agent.client = Summarizer()
    for i in range(20):
        message = Message("user" if i % 2 == 0 else "assistant", f"original {i} " + "x" * 40_000)
        app.conversation.history.append(message)
        app.session.append(message)
    before = copy.deepcopy(app.conversation.history)
    original_append_record = Session.append_record

    def fail_boundary(self, record):
        if record.type == RecordType.COMPACT_BOUNDARY:
            raise OSError("boundary disk full")
        return original_append_record(self, record)

    monkeypatch.setattr(Session, "append_record", fail_boundary)
    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command("continue")
        task = app._agent_task
        with pytest.raises(OSError, match="boundary disk full"):
            await task
        assert app.conversation.history == before + [Message("user", "continue")]
        restored = app.session_manager.resume(app.session.session_id)
        try:
            assert restored.messages == app.conversation.history
        finally:
            restored.session.close()


@pytest.mark.asyncio
async def test_notification_retry_does_not_duplicate_a_record_when_only_metadata_failed(setup, monkeypatch):
    from nanocursor.memory.session import SessionMeta

    app = make_app(setup)
    app.task_manager.current_session_id = app.session.session_id
    task_id = app.task_manager.launch(setup.agent, "background")
    await app.task_manager._async_tasks[task_id]
    await asyncio.sleep(0)
    original_save = SessionMeta.save
    failures = 0

    def fail_once(self, path):
        nonlocal failures
        if self is app.session.meta and failures == 0:
            failures += 1
            raise OSError("metadata temporarily unavailable")
        return original_save(self, path)

    async with app.run_test() as pilot:
        show_chat(app)
        monkeypatch.setattr(SessionMeta, "save", fail_once)
        errors = []
        monkeypatch.setattr(app, "_show_error", errors.append)
        await app._process_task_notifications(start_run=False)
        assert any("metadata temporarily unavailable" in error for error in errors)
        await app._process_task_notifications(start_run=False)
        restored = app.session_manager.resume(app.session.session_id)
        try:
            assert len([message for message in restored.messages if task_id in message.content]) == 1
            assert len([message for message in app.conversation.history if task_id in message.content]) == 1
        finally:
            restored.session.close()
