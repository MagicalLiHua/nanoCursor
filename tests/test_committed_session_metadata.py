"""Metadata failures must not undo JSONL boundaries that already committed."""
import copy
import json

import pytest

from nanocursor.client import LLMClient
from nanocursor.context.manager import SUMMARY_PROMPT
from nanocursor.conversation import Message
from nanocursor.filehistory import FileHistory
from nanocursor.memory.session import RecordType, SessionMeta
from nanocursor.recovery import RecoveryRuntime, RecoveryStore
from nanocursor.tools.base import StreamEnd, TextDelta
from nanocursor.tools.write_file import Params, WriteFile
from test_approval_ui import make_app
from test_auto_approval import setup
from test_status_ui import show_chat


def fail_boundary_metadata_once(app, monkeypatch, record_type, *, nth_save=1):
    original_save = SessionMeta.save
    state = {"boundary_saves": 0, "failed": False}
    jsonl = app.session._sessions_dir / f"{app.session.session_id}.jsonl"

    def save(self, path):
        if self is app.session.meta and not state["failed"]:
            lines = jsonl.read_text().splitlines()
            if lines and json.loads(lines[-1])["type"] == record_type.value:
                state["boundary_saves"] += 1
                if state["boundary_saves"] == nth_save:
                    state["failed"] = True
                    raise OSError("metadata test failure after committed boundary")
        return original_save(self, path)

    monkeypatch.setattr(SessionMeta, "save", save)
    return state, jsonl


class CompactClient(LLMClient):
    async def stream(self, conversation, *, system="", **kwargs):
        if system == SUMMARY_PROMPT:
            yield TextDelta("<summary>published summary</summary>")
        else:
            yield TextDelta("continued after compaction")
        yield StreamEnd("end_turn", input_tokens=10, output_tokens=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["automatic", "manual"])
async def test_compaction_keeps_committed_boundary_despite_metadata_error(setup, monkeypatch, mode):
    app = make_app(setup)
    app.agent.client = CompactClient()
    for i in range(20):
        message = Message("user" if i % 2 == 0 else "assistant", f"original {i} " + "x" * 40_000)
        app.conversation.history.append(message)
        app.session.append(message)
    notices = []
    monkeypatch.setattr(app, "_show_error", notices.append)
    monkeypatch.setattr(app, "add_system_message", notices.append)
    state, jsonl = fail_boundary_metadata_once(app, monkeypatch, RecordType.COMPACT_BOUNDARY)

    async with app.run_test() as pilot:
        show_chat(app)
        if mode == "automatic":
            await app._dispatch_command("continue")
            task = app._agent_task
        else:
            await app._dispatch_command("/compact")
            task = app._command_task
        assert task is not None
        await task
        assert state["failed"]
        assert any("metadata test failure" in notice for notice in notices)
        assert len(app.conversation.history) < 20
        assert "published summary" in app.conversation.history[0].content
        assert not any("original 0 " in message.content for message in app.conversation.history)
        if mode == "automatic":
            assert app.conversation.history[-1].content == "continued after compaction"
        restored = app.session_manager.resume(app.session.session_id)
        try:
            assert "published summary" in restored.messages[0].content
            assert not any("original 0 " in message.content for message in restored.messages)
            assert restored.messages[1:] == app.conversation.history[1:]
        finally:
            restored.session.close()
        records = [json.loads(line) for line in jsonl.read_text().splitlines()]
        assert sum(record["type"] == RecordType.COMPACT_BOUNDARY.value for record in records) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [1, 2])
@pytest.mark.parametrize("nth_save", [1, 2])
async def test_rewind_keeps_committed_history_after_either_metadata_save_fails(setup, monkeypatch, option, nth_save):
    app = make_app(setup)
    app.recovery_runtime = RecoveryRuntime.acquire(setup.root, session=app.session,
                                                   store=RecoveryStore(setup.root / ".test-recovery"))
    app.agent.recovery = app.recovery_runtime
    app.session_manager.recovery_runtime = app.recovery_runtime
    app.agent.file_history = FileHistory(str(setup.root), app.session.session_id,
                                         store=app.recovery_runtime.store,
                                         workspace_id=app.recovery_runtime.workspace_id)
    app.recovery_runtime.file_history = app.agent.file_history
    writer = WriteFile(file_history=app.agent.file_history)
    target = setup.root / "rewind.txt"
    assert not (await writer.execute(Params(file_path=str(target), content="checkpoint content"))).is_error
    for message in [Message("user", "checkpoint request"), Message("assistant", "checkpoint reply")]:
        app.conversation.history.append(message)
        app.session.append(message)
    expected = copy.deepcopy(app.conversation.history)
    checkpoint = app.agent.file_history.make_snapshot(2, "checkpoint", conversation=app.conversation.history)
    if option == 2:
        expected.append(Message("user", "<system-reminder>Only conversation history was rewound. File changes and external effects were not undone.</system-reminder>"))
    assert not (await writer.execute(Params(file_path=str(target), content="later content"))).is_error
    for message in [Message("user", "later request"), Message("assistant", "later reply")]:
        app.conversation.history.append(message)
        app.session.append(message)
    app.conversation.record_usage_anchor(50_000)
    notices = []
    monkeypatch.setattr(app, "add_system_message", notices.append)
    state, jsonl = fail_boundary_metadata_once(app, monkeypatch, RecordType.HISTORY_BOUNDARY, nth_save=nth_save)

    async with app.run_test() as pilot:
        show_chat(app)
        await app._dispatch_command(f"/rewind {checkpoint.checkpoint_id} {option} apply")
        assert state["failed"]
        assert any("metadata test failure" in notice for notice in notices)
        assert app.conversation.history == expected
        assert app.conversation.baseline_tokens == 0
        assert target.read_text() == ("checkpoint content" if option == 1 else "later content")
        assert not app.agent.file_history.pending_restores()
        assert not app.agent.approval_controller.authorization.complete
        restored = app.session_manager.resume(app.session.session_id)
        try:
            assert restored.messages == expected
            assert not restored.session.load_approval_context().complete
        finally:
            restored.session.close()
        records = [json.loads(line) for line in jsonl.read_text().splitlines()]
        assert sum(record["type"] == RecordType.HISTORY_BOUNDARY.value for record in records) == 1
