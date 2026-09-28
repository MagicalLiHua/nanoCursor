"""Failure boundaries for durable file edits and explicitly resumed restores."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from nanocursor.commands.handlers.rewind import _handle_rewind
from nanocursor.conversation import ConversationManager, Message
from nanocursor.filehistory.history import FileHistory, RewindError
from nanocursor.recovery import RecoveryRuntime, RecoveryStorageError, RecoveryStore
from nanocursor.tools.edit_file import EditFile, Params as EditParams
from nanocursor.tools.write_file import WriteFile, Params


def setup_history(tmp_path, session="s", store=None):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    store = store or RecoveryStore(tmp_path / "recovery")
    history = FileHistory(str(root), session, store=store)
    return root, store, history, WriteFile(file_history=history)


async def write(writer, path, content):
    result = await writer.execute(Params(file_path=str(path), content=content))
    assert not result.is_error, result.output


@pytest.mark.asyncio
async def test_first_task_origin_and_versions_survive_restart(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("uncommitted original")
    path.chmod(0o754)
    point = history.begin_checkpoint(0, "first task", conversation=[])
    await write(writer, path, "agent replacement")
    await write(writer, root / "new", "new file")
    store.close()
    reopened = RecoveryStore(tmp_path / "recovery")
    history = FileHistory(str(root), "s", store=reopened)
    assert history.snapshot(point.checkpoint_id).conversation == []
    history.rewind(point.checkpoint_id)
    assert path.read_text() == "uncommitted original"
    assert path.stat().st_mode & 0o777 == 0o754
    assert not (root / "new").exists()
    # No future checkpoint or immutable file evidence was truncated.
    assert history.snapshot(point.checkpoint_id)
    assert len(reopened.rows("SELECT * FROM file_edit_records")) == 2


@pytest.mark.asyncio
async def test_failed_backup_never_permits_unprotected_write(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("original")
    history.begin_checkpoint(0, "task")
    def fail(phase):
        if phase == "blob_before_publish":
            raise OSError("disk full")
    store.fault_hook = fail
    result = await writer.execute(Params(file_path=str(path), content="replacement"))
    assert result.is_error and path.read_text() == "original"
    assert not store.rows("SELECT * FROM file_edit_records")


@pytest.mark.asyncio
async def test_replaced_file_with_missing_applied_marker_can_be_inspected(tmp_path, monkeypatch):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("original")
    point = history.begin_checkpoint(0, "task")
    def failed_marker(edit_id):
        raise RecoveryStorageError("injected result commit failure")
    monkeypatch.setattr(history, "applied_edit", failed_marker)
    result = await writer.execute(Params(file_path=str(path), content="replacement"))
    assert result.is_error and path.read_text() == "replacement"
    assert store.rows("SELECT state FROM file_edit_records")[0]["state"] == "prepared"
    restored = FileHistory(str(root), "s", store=store)
    preview = restored.preview(point.checkpoint_id)
    assert not preview.conflicts and len(preview.files) == 1
    restored.rewind(point.checkpoint_id)
    assert path.read_text() == "original"


@pytest.mark.asyncio
async def test_external_boundary_remains_after_latest_segment_is_restored(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("A")
    old = history.begin_checkpoint(0, "task")
    await write(writer, path, "B")
    path.write_text("U")
    await write(writer, path, "C")
    assert "external edit boundary" in history.preview(old.checkpoint_id).conflicts[0]
    latest = history.get_snapshots()[-1]
    assert "external baseline" in latest.user_text
    history.rewind(latest.checkpoint_id)
    assert path.read_text() == "U"
    # Even manually recreating an old after hash does not erase the barrier.
    path.write_text("B")
    assert history.preview(old.checkpoint_id).conflicts
    reopened = FileHistory(str(root), "s", store=store)
    assert reopened.preview(old.checkpoint_id).conflicts


@pytest.mark.asyncio
async def test_another_sessions_changes_cannot_be_undone_by_original_session(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("A")
    old = history.begin_checkpoint(0, "one")
    await write(writer, path, "B")
    other = FileHistory(str(root), "other", store=store)
    other.begin_checkpoint(0, "two")
    await write(WriteFile(file_history=other), path, "C")
    assert "another session" in history.preview(old.checkpoint_id).conflicts[0]
    assert path.read_text() == "C"


@pytest.mark.asyncio
async def test_restore_resumes_after_replace_before_progress_commit(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    first, second = root / "a", root / "b"
    first.write_text("A")
    second.write_text("A")
    point = history.begin_checkpoint(0, "two file task")
    await write(writer, first, "B")
    await write(writer, second, "B")
    restore_id = history.start_restore(point.checkpoint_id)
    def fail_after_replace(phase):
        if phase == "before_commit" and first.read_text() == "A":
            raise OSError("crashed before progress commit")
    store.fault_hook = fail_after_replace
    with pytest.raises(RewindError, match="interrupted"):
        history.apply_restore(restore_id)
    store.fault_hook = None
    assert first.read_text() == "A" and second.read_text() == "B"
    store.close()
    store = RecoveryStore(tmp_path / "recovery")
    restarted = FileHistory(str(root), "s", store=store)
    info = restarted.restore_info(restore_id)
    assert [item["observed"] for item in info["items"]] == ["at_target", "not_restored"]
    assert info["items"][0]["state"] == "pending"
    restarted.apply_restore(restore_id)
    restarted.complete_restore(restore_id)
    assert first.read_text() == second.read_text() == "A"
    assert not restarted.pending_restores()
    assert restarted.apply_restore(restore_id) == []
    # Safety copies are durably retained, including the state being undone.
    original_item = info["items"][0]
    assert store.get_blob(json.loads(original_item["before_state"])["digest"]) == b"B"


@pytest.mark.asyncio
async def test_resume_conflict_stops_before_remaining_file(tmp_path, monkeypatch):
    root, store, history, writer = setup_history(tmp_path)
    paths = [root / "a", root / "b"]
    for path in paths:
        path.write_text("A")
    point = history.begin_checkpoint(0, "task")
    for path in paths:
        await write(writer, path, "B")
    restore_id = history.start_restore(point.checkpoint_id)
    original = os.replace
    def replace(src, dst):
        if Path(dst) == paths[1]:
            raise OSError("injected failure")
        return original(src, dst)
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        with pytest.raises(RewindError):
            history.apply_restore(restore_id)
    paths[0].write_text("user's new changes")
    with pytest.raises(RewindError, match="outside the agent"):
        history.apply_restore(restore_id)
    assert paths[0].read_text() == "user's new changes" and paths[1].read_text() == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["symlink", "parent_symlink", "hardlink", "directory"])
async def test_unsupported_file_paths_fail_before_backup_or_write(tmp_path, kind):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    other = root / "other"
    other.write_text("private")
    if kind == "symlink":
        path.symlink_to(other)
    elif kind == "parent_symlink":
        directory = root / "real"
        directory.mkdir()
        (root / "link").symlink_to(directory, target_is_directory=True)
        path = root / "link" / "file"
    elif kind == "hardlink":
        os.link(other, path)
    else:
        path.mkdir()
    result = await writer.execute(Params(file_path=str(path), content="danger"))
    assert result.is_error
    assert other.read_text() == "private"
    assert not store.rows("SELECT * FROM file_edit_records")


@pytest.mark.asyncio
async def test_project_external_write_is_explicitly_not_in_rewind(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    point = history.begin_checkpoint(0, "task")
    path = tmp_path / "outside"
    result = await writer.execute(Params(file_path=str(path), content="allowed"))
    assert not result.is_error and "not covered" in result.output
    preview = history.preview(point.checkpoint_id)
    assert not preview.files
    assert any(str(path) in notice for notice in preview.uncovered)
    history.rewind(point.checkpoint_id)
    assert path.read_text() == "allowed"


@pytest.mark.asyncio
async def test_edit_cannot_overwrite_change_between_read_and_backup(tmp_path, monkeypatch):
    root, store, history, _ = setup_history(tmp_path)
    path = root / "file"
    path.write_text("A")
    editor = EditFile(file_history=history)
    original = history.prepare_edit
    def race(path_arg, content, **kwargs):
        path.write_text("user changes")
        return original(path_arg, content, **kwargs)
    monkeypatch.setattr(history, "prepare_edit", race)
    result = await editor.execute(EditParams(file_path=str(path), old_string="A", new_string="B"))
    assert result.is_error and path.read_text() == "user changes"


@pytest.mark.asyncio
async def test_rewind_preview_is_readonly_and_requires_stable_confirmation(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("A")
    point = history.begin_checkpoint(0, "task", conversation=[])
    await write(writer, path, "B")
    ui = NS(add_system_message=Mock())
    context = NS(agent=NS(file_history=history, _file_versions={}, recovery=None), args="1 3", ui=ui,
                 config={}, session=None, conversation=ConversationManager())
    await _handle_rewind(context)
    assert path.read_text() == "B" and not history.pending_restores()
    assert "Preview only" in ui.add_system_message.call_args.args[0]
    context.args = "1 3 apply"
    await _handle_rewind(context)
    assert path.read_text() == "B"
    context.args = f"{point.checkpoint_id} 3 apply"
    await _handle_rewind(context)
    assert path.read_text() == "A"
    assert "not covered" in context.conversation.history[-1].content


@pytest.mark.asyncio
async def test_conversation_projection_is_idempotent_when_restore_completion_crashes(tmp_path, monkeypatch):
    from nanocursor.memory.session import SessionManager
    root, store, history, writer = setup_history(tmp_path)
    session = SessionManager(str(root)).create()
    history = FileHistory(str(root), session.session_id, store=store)
    runtime = RecoveryRuntime.acquire(root, session=session, store=store)
    runtime.file_history = history
    conversation = ConversationManager()
    conversation.add_user_message("old")
    point = history.begin_checkpoint(1, "task", conversation=conversation.history)
    conversation.add_user_message("later")
    for message in conversation.history:
        session.append(message)
    a = NS(file_history=history, recovery=runtime, _file_versions={}, approval_controller=None,
           clear_active_skills=Mock())
    context = NS(agent=a, args=f"{point.checkpoint_id} 1 apply", ui=NS(add_system_message=Mock()),
                 config={}, session=session, conversation=conversation)
    original = history.complete_restore
    def fail(restore_id):
        raise RecoveryStorageError("after conversation projection")
    monkeypatch.setattr(history, "complete_restore", fail)
    await _handle_rewind(context)
    restore_id = history.pending_restores()[0]["restore_id"]
    generation = runtime.generation
    record_id = history.restore_info(restore_id)["conversation_record_id"]
    assert len(store.rows("SELECT * FROM outbox WHERE record_id=?", (record_id,))) == 1
    monkeypatch.setattr(history, "complete_restore", original)
    context.args = f"resume {restore_id} apply"
    await _handle_rewind(context)
    assert not history.pending_restores()
    assert runtime.generation == generation
    assert len(store.rows("SELECT * FROM outbox WHERE record_id=?", (record_id,))) == 1
    assert conversation.history == [Message("user", "old")]
    session.close()
    runtime.close()


@pytest.mark.asyncio
async def test_abandoned_future_checkpoint_does_not_restore_wrong_branch(tmp_path):
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("A")
    origin = history.begin_checkpoint(0, "origin")
    await write(writer, path, "B")
    future = history.begin_checkpoint(1, "future")
    await write(writer, path, "C")
    history.rewind(origin.checkpoint_id)
    assert path.read_text() == "A"
    restarted = FileHistory(str(root), "s", store=store)
    assert "previous branch" in restarted.preview(future.checkpoint_id).conflicts[0]
    assert restarted.snapshot(future.checkpoint_id).backups[str(path)].digest
    retained = restarted.inspect_checkpoint(future.checkpoint_id)
    assert Path(retained["edits"][0]["after"]["backup_path"]).read_text() == "C"
    assert path.read_text() == "A"


def test_retention_preserves_pins_and_relinks_empty_checkpoint_ancestry(tmp_path):
    root, store, history, _ = setup_history(tmp_path)
    pinned = history.begin_checkpoint(0, "keep", conversation=[])
    history.pin_checkpoint(pinned.checkpoint_id)
    empty = history.begin_checkpoint(1, "old empty", conversation=[])
    history.begin_checkpoint(2, "another empty", conversation=[])
    latest = history.begin_checkpoint(2, "current", conversation=[])
    assert history.prune_unreferenced(keep=0) == 2
    assert history.snapshot(pinned.checkpoint_id).pinned
    with pytest.raises(RewindError):
        history.snapshot(empty.checkpoint_id)
    assert history.snapshot(latest.checkpoint_id).parent_id == pinned.checkpoint_id
    assert not history.preview(pinned.checkpoint_id).conflicts


@pytest.mark.asyncio
async def test_pending_restore_blocks_new_execution_but_allows_explicit_resume(tmp_path):
    from nanocursor.recovery import RecoveryRequired
    root, store, history, writer = setup_history(tmp_path)
    path = root / "file"
    path.write_text("A")
    point = history.begin_checkpoint(0, "task")
    await write(writer, path, "B")
    restore_id = history.start_restore(point.checkpoint_id)
    runtime = RecoveryRuntime.acquire(root, store=store)
    try:
        with pytest.raises(RecoveryRequired):
            runtime.begin_run()
        runtime.ensure_workspace_idle(allow_restore_id=restore_id)
        history.apply_restore(restore_id)
        history.complete_restore(restore_id)
        runtime.ensure_ready()
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_file_tools_use_runtime_workspace_instead_of_shared_instance(tmp_path):
    root, store, first, writer = setup_history(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    second = FileHistory(str(other_root), "child", store=store)
    second.begin_checkpoint(0, "child task")
    runtime = RecoveryRuntime.acquire(other_root, store=store)
    runtime.file_history = second
    try:
        with runtime.activate():
            await write(writer, other_root / "file", "child file")
        rows = store.rows("SELECT workspace_id FROM file_edit_records")
        assert [r["workspace_id"] for r in rows] == [second.workspace_id]
        assert not first.get_snapshots()
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_write_rechecks_observed_version_after_backup(tmp_path, monkeypatch):
    from nanocursor.tools.file_state_cache import FileStateCache
    root, store, history, _ = setup_history(tmp_path)
    path = root / "file"
    path.write_text("original")
    cache = FileStateCache()
    cache.update(str(path))
    writer = WriteFile(file_history=history, file_state_cache=cache)
    original = history.prepare_edit
    def race(path_arg, content, **kwargs):
        path.write_text("new user work")
        return original(path_arg, content, **kwargs)
    monkeypatch.setattr(history, "prepare_edit", race)
    result = await writer.execute(Params(file_path=str(path), content="agent work"))
    assert result.is_error and path.read_text() == "new user work"


@pytest.mark.asyncio
@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='macOS system aliases')
@pytest.mark.parametrize('alias_name', ['var', 'tmp'])
async def test_macos_system_temporary_aliases_allow_protected_file_edits(tmp_path, alias_name):
    import tempfile
    parent = str(tmp_path).replace('/private/var/', '/var/', 1) if alias_name == 'var' else '/tmp'
    with tempfile.TemporaryDirectory(prefix='nanocursor-system-alias-', dir=parent) as directory:
        # /var/folders and /tmp are normal user-supplied temporary paths on macOS.
        alias_root = Path(directory)
        canonical_root = alias_root.resolve()
        store = RecoveryStore(tmp_path / 'state')
        history = FileHistory(str(alias_root), 'alias', store=store)
        point = history.begin_checkpoint(0, 'edit through OS alias')
        writer = WriteFile(file_history=history)
        result = await writer.execute(Params(file_path=str(alias_root / 'file'), content='first'))
        assert not result.is_error, result.output
        editor = EditFile(file_history=history)
        result = await editor.execute(EditParams(file_path=str(alias_root / 'file'), old_string='first', new_string='second'))
        assert not result.is_error, result.output
        assert (canonical_root / 'file').read_text() == 'second'
        assert not history.preview(point.checkpoint_id).conflicts
        history.rewind(point.checkpoint_id)
        assert not (canonical_root / 'file').exists()
        store.close()
