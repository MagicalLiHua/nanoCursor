import copy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from nanocursor.commands.handlers.rewind import _handle_rewind
from nanocursor.conversation import ConversationManager, Message, ThinkingBlock
from nanocursor.filehistory.history import FileHistory, RewindError
from nanocursor.memory.session import SessionManager
from nanocursor.tools.write_file import WriteFile, Params
from test_execution_boundaries import ScriptClient, agent, drive, registry_for
from nanocursor.tools.base import ToolCallComplete


def state(tmp_path):
    history = FileHistory(str(tmp_path), "test")
    return history, WriteFile(file_history=history)


async def write(writer, path, content):
    result = await writer.execute(Params(file_path=str(path), content=content))
    assert not result.is_error, result.output


@pytest.mark.asyncio
async def test_rewind_restores_completed_state_and_later_first_edits(tmp_path):
    history, writer = state(tmp_path)
    existing, later, created = (tmp_path / name for name in ["existing", "later", "created"])
    existing.write_text("A")
    existing.chmod(0o755)
    later.write_text("original")
    await write(writer, existing, "B")
    history.make_snapshot(3, "B")
    await write(writer, existing, "C")
    await write(writer, later, "changed")
    await write(writer, created, "new")
    history.make_snapshot(6, "C")
    changed = history.rewind(0)
    assert set(changed) == {str(existing), str(later), str(created)}
    assert existing.read_text() == "B" and existing.stat().st_mode & 0o777 == 0o755
    assert later.read_text() == "original" and not created.exists()
    assert history.rewind(0) == []
    # Branching after a rewind must never overwrite an older immutable backup.
    await write(writer, existing, "D")
    history.make_snapshot(4, "D")
    history.rewind(0)
    assert existing.read_text() == "B"


@pytest.mark.asyncio
async def test_external_edit_blocks_entire_restore(tmp_path):
    history, writer = state(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    await write(writer, a, "first")
    await write(writer, b, "first")
    history.make_snapshot(1, "first")
    await write(writer, a, "second")
    await write(writer, b, "second")
    b.write_text("user changes")
    with pytest.raises(RewindError, match="outside the agent"):
        history.rewind(0)
    assert a.read_text() == "second" and b.read_text() == "user changes"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
async def test_bad_backup_never_means_delete_file(tmp_path, damage):
    history, writer = state(tmp_path)
    path = tmp_path / "file"
    await write(writer, path, "first")
    history.make_snapshot(1, "first")
    backup = Path(history.get_snapshots()[0].backups[str(path)].backup_path)
    await write(writer, path, "second")
    if damage == "missing":
        backup.unlink()
    else:
        backup.write_text("corrupt")
    with pytest.raises(RewindError, match="backup"):
        history.rewind(0)
    assert path.read_text() == "second"


@pytest.mark.asyncio
async def test_rewind_does_not_follow_replaced_symlink(tmp_path):
    history, writer = state(tmp_path)
    path, other = tmp_path / "file", tmp_path / "other"
    await write(writer, path, "first")
    history.make_snapshot(1, "first")
    await write(writer, path, "second")
    other.write_text("private")
    path.unlink()
    path.symlink_to(other)
    with pytest.raises(RewindError, match="outside the agent"):
        history.rewind(0)
    assert other.read_text() == "private" and path.is_symlink()


@pytest.mark.asyncio
async def test_rewind_conversation_after_compaction_persists_exact_state(tmp_path):
    history, writer = state(tmp_path)
    path = tmp_path / "file"
    conv = ConversationManager()
    conv.add_user_message("write first")
    conv.add_assistant_message("wrote first", thinking_blocks=[ThinkingBlock("reason", "signature")])
    await write(writer, path, "first")
    history.make_snapshot(len(conv.history), "first", conversation=conv.history)
    expected = copy.deepcopy(conv.history)
    await write(writer, path, "second")
    # Replacing/compacting the live history invalidates numeric indexes only;
    # the checkpoint must retain its actual messages.
    conv.replace_history([Message("user", "compacted later state")])
    sm = SessionManager(str(tmp_path))
    session = sm.create()
    for message in conv.history:
        session.append(message)
    a = agent(tmp_path)
    a.file_history = history
    ctx = NS(agent=a, conversation=conv, session=session, args="1 1", ui=NS(add_system_message=Mock()), config={})
    await _handle_rewind(ctx)
    assert path.read_text() == "first" and conv.history == expected
    session.close()
    resumed = sm.resume(session.session_id)
    assert resumed.messages == expected
    resumed.session.close()


@pytest.mark.asyncio
async def test_invalid_rewind_option_does_not_change_files(tmp_path):
    history, writer = state(tmp_path)
    path = tmp_path / "file"
    await write(writer, path, "first")
    history.make_snapshot(1, "first", conversation=[])
    await write(writer, path, "second")
    ctx = NS(agent=NS(file_history=history), args="1 typo", ui=NS(add_system_message=Mock()))
    await _handle_rewind(ctx)
    assert path.read_text() == "second"


def test_switching_session_resets_file_checkpoints(tmp_path, monkeypatch):
    from nanocursor.app import NanoCursorApp
    from nanocursor.config import ProviderConfig
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    a = agent(tmp_path)
    a.file_history = FileHistory(str(tmp_path), "old")
    a.file_history.make_snapshot(1, "old", conversation=[])
    app = NanoCursorApp([ProviderConfig("test", "openai-compat", "http://127.0.0.1:1", "test", api_key="test")])
    app.agent = a
    session = SessionManager(str(tmp_path)).create()
    app._set_session(session)
    assert not a.file_history.has_snapshots()
    assert a.file_history is app.file_history
    assert all(t.file_history is a.file_history for t in app.registry.list_tools() if hasattr(t, "file_history"))
    session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [1, 2, 3])
async def test_completed_agent_checkpoint_and_restore_options(tmp_path, option):
    history, writer = state(tmp_path)
    path = tmp_path / "file"
    client = ScriptClient([ToolCallComplete("write", "WriteFile", {"file_path": str(path), "content": "first"})])
    a = agent(tmp_path, client, registry_for(writer))
    a.file_history = history
    conv, _ = await drive(a)
    checkpoint_messages = copy.deepcopy(conv.history)
    assert history.get_snapshots()[0].conversation == checkpoint_messages
    await write(writer, path, "second")
    conv.add_user_message("later request")
    current_messages = copy.deepcopy(conv.history)
    controller = NS(authorization=NS(complete=True), revision=7, persist_authorization=Mock())
    a.approval_controller = controller
    ctx = NS(agent=a, conversation=conv, session=None, args=f"1 {option}",
             ui=NS(add_system_message=Mock()), config={})
    await _handle_rewind(ctx)
    assert path.read_text() == ("second" if option == 2 else "first")
    assert conv.history == (current_messages if option == 3 else checkpoint_messages)
    if option in (1, 2):
        assert not controller.authorization.complete and controller.revision == 8
        controller.persist_authorization.assert_called_once()
    else:
        assert controller.authorization.complete and controller.revision == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rewind", "clear", "session"])
async def test_history_changes_wait_for_background_tasks(operation):
    from nanocursor.commands.handlers.clear import handle_clear
    from nanocursor.commands.handlers.session import handle_session
    handlers = {"rewind": _handle_rewind, "clear": handle_clear, "session": handle_session}
    session, manager = Mock(), Mock()
    ui = NS(add_system_message=Mock(), task_manager=NS(_async_tasks={"running": NS(done=lambda: False)}))
    ctx = NS(ui=ui, session=session, session_manager=manager, args="new")
    await handlers[operation](ctx)
    session.close.assert_not_called()
    manager.create.assert_not_called()
    assert "Wait for" in ui.add_system_message.call_args.args[0]
