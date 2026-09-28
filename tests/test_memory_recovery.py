"""Memory maintenance journals publication, not time spent awaiting a model."""
import asyncio

import pytest

from nanocursor.conversation import ConversationManager
from nanocursor.memory.auto_memory import MemoryManager
from nanocursor.memory.consolidation import MemoryConsolidator
from nanocursor.memory.store import MemoryStore
from nanocursor.recovery import RecoveryRequired, RecoveryRuntime, RecoveryStore, RecoveryStorageError
from test_memory_storage import ExtractClient, conversation
from test_consolidation import memory_project, ProposalClient


@pytest.fixture
def maintenance(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    runtime = RecoveryRuntime.acquire(project, store=RecoveryStore(tmp_path / "state"))
    yield runtime, MemoryManager(str(project))
    runtime.close()
    runtime.store.close()


@pytest.mark.asyncio
async def test_extract_cancellation_during_model_wait_has_no_unknown_effect(maintenance):
    runtime, manager = maintenance
    entered = asyncio.Event()
    async def wait():
        entered.set()
        await asyncio.Event().wait()
    with runtime.activate():
        task = asyncio.create_task(manager.extract(ExtractClient(before=wait), conversation(), "openai-compat"))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not runtime.pending()
    assert not runtime.store.rows("SELECT * FROM operations")
    assert not manager.project_mem_dir.exists()


@pytest.mark.asyncio
async def test_extraction_records_actual_body_and_index_publications(maintenance):
    runtime, manager = maintenance
    with runtime.activate():
        await manager.extract(ExtractClient(), conversation(), "openai-compat")
    rows = runtime.store.rows("SELECT * FROM operations ORDER BY created_at")
    assert len(rows) == 2
    assert all(row["kind"] == "maintenance" and row["state"] == "completed" for row in rows)
    assert all(row["name"] == "memory.write" for row in rows)
    assert "Python 3.11" in (manager.project_mem_dir / "preference.md").read_text()
    assert not runtime.pending()


@pytest.mark.asyncio
async def test_unknown_gate_precedes_extraction_model_request(maintenance):
    runtime, manager = maintenance
    operation = runtime.begin_operation("tool", "MCP.mutate", {})
    runtime.mark_unknown(operation, "connection lost")
    called = False
    async def before():
        nonlocal called
        called = True
    with runtime.activate(), pytest.raises(RecoveryRequired):
        await manager.extract(ExtractClient(before=before), conversation(), "openai-compat")
    assert not called and not manager.project_mem_dir.exists()


@pytest.mark.asyncio
async def test_failed_index_publish_keeps_known_body_and_unknown_index(maintenance, monkeypatch):
    import nanocursor.memory.store as storage
    runtime, manager = maintenance
    replace = storage.os.replace
    def fail_index(source, destination, *args, **kwargs):
        if destination == "MEMORY.md":
            raise OSError("publication fault")
        return replace(source, destination, *args, **kwargs)
    monkeypatch.setattr(storage.os, "replace", fail_index)
    with runtime.activate():
        await manager.extract(ExtractClient(), conversation(), "openai-compat")
    rows = runtime.store.rows("SELECT * FROM operations ORDER BY created_at")
    assert [row["state"] for row in rows] == ["completed", "outcome_unknown"]
    assert len(runtime.pending()) == 1
    assert not (manager.project_mem_dir / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_storage_failure_is_not_swallowed_by_extractor(maintenance):
    runtime, manager = maintenance
    def fault(phase):
        if phase == "before_commit":
            raise OSError("journal full")
    runtime.store.fault_hook = fault
    with runtime.activate(), pytest.raises(RecoveryStorageError):
        await manager.extract(ExtractClient(), conversation(), "openai-compat")
    assert not manager.project_mem_dir.exists()


@pytest.mark.asyncio
async def test_consolidation_model_cancellation_leaves_no_live_publication(memory_project, tmp_path):
    project, root = memory_project
    MemoryStore.from_directory(root).migrate()
    runtime = RecoveryRuntime.acquire(project, store=RecoveryStore(tmp_path / "state"))
    item = MemoryConsolidator(str(project), enabled=True, clock=lambda: 1_800_000_000)
    entered = asyncio.Event()
    async def wait(_):
        entered.set()
        await asyncio.Event().wait()
    try:
        with runtime.activate():
            task = asyncio.create_task(item.maybe_run(ProposalClient(before=wait), ConversationManager(), "openai-compat"))
            await asyncio.wait_for(entered.wait(), 2)
            await item.cancel_and_wait()
            assert task.cancelled()
        assert not runtime.pending()
        assert not runtime.store.rows("SELECT * FROM operations WHERE state='intent'")
    finally:
        runtime.close()
        runtime.store.close()


def test_clear_records_each_removal_with_observed_result(maintenance):
    runtime, manager = maintenance
    store = MemoryStore.from_directory(manager.project_mem_dir)
    store.write_memories([("preference", "project", "desc", "body")], store.snapshot())
    with runtime.activate():
        store.clear()
    rows = runtime.store.rows("SELECT * FROM operations")
    assert len(rows) == 3
    assert all(row["name"] == "memory.remove" and row["state"] == "completed" for row in rows)
    assert not store.snapshot().records
