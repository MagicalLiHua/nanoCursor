"""Regression paths for extraction boundaries and the shared active set."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanocursor.conversation import ConversationManager
from nanocursor.memory.auto_memory import MemoryManager
from nanocursor.memory.recall import RelevantMemory, render_reminder, scan_memory_files
from nanocursor.memory.store import INDEX_MARKER, MemoryConflict, MemoryStorageError, MemoryStore, active_records
from nanocursor.tools.base import StreamEnd, TextDelta, ToolCallStart


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    return MemoryManager(str(project))


class ExtractClient:
    def __init__(self, name="preference", response=None, before=None, end="end_turn"):
        self.response = response or (f"MEMORY_NAME: {name}\nMEMORY_TYPE: project\n"
                                     "MEMORY_DESC: Use Python\nMEMORY_BODY: Use Python 3.11.\n"
                                     "Keep tests fast.\n---\n")
        self.before = before
        self.end = end

    async def stream(self, *args, **kwargs):
        if self.before:
            await self.before()
        yield TextDelta(self.response)
        if self.end:
            yield StreamEnd(self.end)


def conversation():
    result = ConversationManager()
    result.add_user_message("Remember that this project uses Python 3.11")
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../escaped", "/tmp/escaped", "a/b", "a\\b", "MEMORY", ".", "..",
                                  "bad\x00name", "a" * 81, "a.md", "name\nother"])
async def test_extraction_rejects_unsafe_names_without_advancing_cursor(manager, name):
    await manager.extract(ExtractClient(name), conversation(), "openai-compat")
    assert manager._last_extraction_msg_count == 0
    assert not manager.project_mem_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["file", "index", "directory", "parent"])
async def test_extraction_rejects_symlinks_and_preserves_outside(manager, tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "preference.md"
    secret.write_text("outside original")
    if component == "parent":
        (manager.project_mem_dir.parent).symlink_to(outside, target_is_directory=True)
    elif component == "directory":
        manager.project_mem_dir.parent.mkdir()
        manager.project_mem_dir.symlink_to(outside, target_is_directory=True)
    else:
        manager.project_mem_dir.mkdir(parents=True)
        target = manager.project_mem_dir / ("preference.md" if component == "file" else "MEMORY.md")
        target.symlink_to(secret)
    await manager.extract(ExtractClient(), conversation(), "openai-compat")
    assert secret.read_text() == "outside original"
    assert manager._last_extraction_msg_count == 0
    assert manager.load_all() == []


@pytest.mark.asyncio
async def test_valid_extract_preserves_multiline_body_and_updates_exact_pointer(manager):
    await manager.extract(ExtractClient(), conversation(), "openai-compat")
    assert manager._last_extraction_msg_count == 1
    assert "Keep tests fast." in (manager.project_mem_dir / "preference.md").read_text()
    # A longer filename containing the same suffix cannot hide a new pointer.
    manager._last_extraction_msg_count = 0
    await manager.extract(ExtractClient("ference"), conversation(), "openai-compat")
    index = manager.project_path.read_text()
    assert "](preference.md)" in index and "](ference.md)" in index
    assert len(manager.load_all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response,end", [("NONE", "max_tokens"), ("NONE", None),
                                           ("Oops no format", "end_turn"),
                                           ("MEMORY_NAME: name", "end_turn")])
async def test_only_valid_complete_extraction_advances_cursor(manager, response, end):
    await manager.extract(ExtractClient(response=response, end=end), conversation(), "openai-compat")
    assert manager._last_extraction_msg_count == 0
    assert not manager.project_mem_dir.exists()


@pytest.mark.asyncio
async def test_valid_none_advances_without_writing(manager):
    await manager.extract(ExtractClient(response="NONE"), conversation(), "openai-compat")
    assert manager._last_extraction_msg_count == 1
    assert not manager.project_mem_dir.exists()


@pytest.mark.asyncio
async def test_capture_does_not_skip_new_messages_arriving_during_request(manager):
    conv = conversation()
    async def append():
        conv.add_user_message("new unsent fact")
    await manager.extract(ExtractClient(response="NONE", before=append), conv, "openai-compat")
    assert manager._last_extraction_msg_count == 1 and len(conv.history) == 2


@pytest.mark.asyncio
async def test_extraction_conflict_preserves_human_edits(manager):
    manager.project_mem_dir.mkdir(parents=True)
    target = manager.project_mem_dir / "preference.md"
    target.write_text("old")
    async def change():
        target.write_text("human change")
    await manager.extract(ExtractClient(before=change), conversation(), "openai-compat")
    assert target.read_text() == "human change"
    assert manager._last_extraction_msg_count == 0


@pytest.mark.asyncio
async def test_index_failure_keeps_body_and_reports_incomplete_extraction(manager, monkeypatch, caplog):
    from nanocursor.memory import store as storage
    write = storage._Directory.write
    def fail(self, name, content, **kwargs):
        if name == "MEMORY.md":
            raise OSError("index failure")
        return write(self, name, content, **kwargs)
    monkeypatch.setattr(storage._Directory, "write", fail)
    await manager.extract(ExtractClient(), conversation(), "openai-compat")
    assert "Use Python" in (manager.project_mem_dir / "preference.md").read_text()
    assert manager._last_extraction_msg_count == 0
    assert "not fully published" in caplog.text


def test_migration_is_atomic_and_keeps_legacy_names_and_notes(manager):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    (root / "中文笔记.md").write_text("legacy body")
    (root / "MEMORY.md").write_text("# Human notes\nDo not erase.\n")
    store = MemoryStore.from_directory(root)
    assert len(store.snapshot().records) == 1
    snapshot = store.migrate()
    assert INDEX_MARKER in snapshot.index and "Do not erase." in snapshot.index
    assert store.migrate() == snapshot
    assert (root / "中文笔记.md").read_text() == "legacy body"
    (root / "unregistered.md").write_text("new unregistered body")
    assert len(manager.load_all()) == len(scan_memory_files(root, "project")) == 1


def test_shared_readers_skip_retired_and_new_unregistered_files(manager):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    for name in ("active", "retired", "orphan"):
        (root / f"{name}.md").write_text(name)
    (root / "MEMORY.md").write_text(INDEX_MARKER + "\n- [Active](active.md) — current\n")
    assert [Path(item.path).name for item in manager.load_all()] == ["active.md"]
    assert [item.filename for item in scan_memory_files(root, "project")] == ["active.md"]
    assert "retired" not in manager._scan_existing_memories()
    reminder = render_reminder([RelevantMemory(str(root / "retired.md"), 0, str(root), "retired.md")])
    assert "retired" not in reminder


def test_directory_swap_does_not_follow_new_symlink(manager, tmp_path):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    store = MemoryStore.from_directory(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    with store.open() as opened:
        moved = root.with_name("moved-memory")
        root.rename(moved)
        root.symlink_to(outside, target_is_directory=True)
        opened.write("safe.md", "inside original directory", new=True)
    assert not (outside / "safe.md").exists()
    assert (moved / "safe.md").read_text() == "inside original directory"
    with pytest.raises(OSError):
        store.snapshot()


def test_managed_empty_retired_target_cannot_be_overwritten(manager):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    (root / "MEMORY.md").write_text(INDEX_MARKER + "\n")
    (root / "retired.md").write_text("")
    store = MemoryStore.from_directory(root)
    with pytest.raises(MemoryConflict):
        store.write_memories([("retired", "project", "new", "new")], store.snapshot())
    assert (root / "retired.md").read_text() == ""


@pytest.mark.asyncio
async def test_recall_selects_exact_scoped_manifest_path(manager):
    from nanocursor.memory.recall import find_relevant_memories
    manager.project_mem_dir.mkdir(parents=True)
    manager.user_mem_dir.mkdir(parents=True)
    for directory, text in ((manager.project_mem_dir, "project fact"), (manager.user_mem_dir, "user fact")):
        (directory / "same.md").write_text(text)
    async def selector(system, payload):
        import json
        assert str(manager.project_mem_dir / "same.md") in payload
        return json.dumps({"selected_memories": ["same.md", str(manager.project_mem_dir / "same.md")]})
    result = await find_relevant_memories("fact", manager.user_mem_dir, manager.project_mem_dir,
                                          None, None, selector)
    assert len(result) == 1
    assert "project fact" in render_reminder(result) and "user fact" not in render_reminder(result)


def test_deleting_empty_source_is_a_conflict(manager):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    (root / "empty.md").write_text("")
    store = MemoryStore.from_directory(root)
    snapshot = store.snapshot()
    (root / "empty.md").unlink()
    with pytest.raises(MemoryConflict, match="removed"):
        store.write_memories([("new", "project", "description", "body")], snapshot)
    assert not (root / "new.md").exists()


def test_legacy_special_filenames_migrate_without_renaming_or_loss(manager):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    names = ["中文 (草稿).md", "[old] notes.md", "literal%20name.md", ".private-note.md"]
    for name in names:
        (root / name).write_text("Body " + name)
    store = MemoryStore.from_directory(root)
    assert {record.filename for record in store.snapshot().records} == set(names)
    store.migrate()
    assert {record.filename for record in store.snapshot().records} == set(names)
    assert all((root / name).read_text() == "Body " + name for name in names)


@pytest.mark.parametrize("target", ["%2e%2e/outside.md", "MEMORY.md", "bad%ff.md"])
def test_invalid_encoded_or_reserved_index_target_fails_closed(manager, target):
    root = manager.project_mem_dir
    root.mkdir(parents=True)
    (root / "MEMORY.md").write_text(INDEX_MARKER + f"\n- [Bad]({target}) — invalid\n")
    with pytest.raises(MemoryStorageError):
        MemoryStore.from_directory(root).snapshot()
    assert not scan_memory_files(root, "project")
