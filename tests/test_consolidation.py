"""Offline tests of the actual consolidator and safe publication path."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nanocursor.conversation import ConversationManager
from nanocursor.memory.consolidation import MemoryConsolidator, STATE_FILE, _list_sessions_since
from nanocursor.memory.store import INDEX_MARKER, MemoryStore, active_records, render_memory
from nanocursor.memory.recall import scan_memory_files
from nanocursor.tools.base import StreamEnd, TextDelta


@pytest.fixture
def memory_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))
    root = project / ".nanocursor" / "memory"
    root.mkdir(parents=True)
    for name, body in (("alpha", "Use Python 3.11"), ("beta", "Python version is 3.11"),
                       ("other", "Use pytest")):
        (root / (name + ".md")).write_text(render_memory(name, body, "project", body))
    (root / "MEMORY.md").write_text("# My notes\nKeep this human explanation.\n- [Alpha](alpha.md) — Python\n")
    sessions = project / ".nanocursor" / "sessions"
    sessions.mkdir()
    for number in range(5):
        sid = f"session_20260926_12000{number}_test"
        stamp = datetime.fromtimestamp(1_700_000_000 + number, timezone.utc).isoformat()
        (sessions / f"{sid}.meta").write_text(json.dumps({"id": sid, "last_active": stamp,
                                                        "title": "Python", "summary": "Use 3.11"}))
        (sessions / f"{sid}.jsonl").write_text(json.dumps({"message": {"role": "user", "content": "Use 3.11"}}))
    return project, root


class ProposalClient:
    max_output_tokens = 8000

    def __init__(self, response=None, before=None, end="end_turn"):
        self.calls = []
        self.response = response
        self.before = before
        self.end = end

    async def stream(self, conversation, system="", tools=None, **kwargs):
        payload = json.loads(conversation.history[0].content)
        self.calls.append((payload, tools, kwargs))
        if self.before:
            await self.before(payload)
        response = self.response(payload) if callable(self.response) else self.response
        if response is None:
            records = [record for record in payload["memories"] if record["filename"] in {"alpha.md", "beta.md"}]
            response = {"schema_version": 1, "noop": False, "groups": [{
                "sources": [record["id"] for record in records], "name": "Python version",
                "description": "Use Python 3.11", "type": payload["scope"] if payload["scope"] == "user" else "project",
                "body": "Use Python 3.11."}]}
        yield TextDelta(response if isinstance(response, str) else json.dumps(response))
        if self.end:
            yield StreamEnd(self.end, input_tokens=42, output_tokens=17)


def consolidator(project, **kwargs):
    return MemoryConsolidator(str(project), enabled=True, clock=lambda: 1_800_000_000, **kwargs)


@pytest.mark.asyncio
async def test_disabled_has_no_io_or_model_calls(memory_project):
    project, root = memory_project
    before = {p: p.read_bytes() for p in root.iterdir()}
    client = ProposalClient()
    item = MemoryConsolidator(str(project))
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert not client.calls
    assert {p: p.read_bytes() for p in root.iterdir()} == before
    assert not (root / STATE_FILE).exists()


@pytest.mark.asyncio
async def test_real_entrypoint_merges_preserves_and_publishes_shared_active_set(memory_project):
    project, root = memory_project
    old = {name: (root / name).read_bytes() for name in ("alpha.md", "beta.md", "other.md")}
    client = ProposalClient()
    item = consolidator(project)
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.status["state"] == "completed", item.status
    index = (root / "MEMORY.md").read_text()
    assert INDEX_MARKER in index and "Keep this human explanation." in index
    assert "](alpha.md)" not in index and "](beta.md)" not in index and "](other.md)" in index
    assert all((root / name).read_bytes() == body for name, body in old.items())
    active = active_records(root)
    assert len(active) == 2 and {record.filename for record in active} == {
        header.filename for header in scan_memory_files(root, "project")}
    assert client.calls[0][1] == [] and client.calls[0][2]["max_output_tokens"] == 4096
    assert client.max_output_tokens == 8000
    assert item.status["input_tokens"] == 42
    assert list(root.parent.glob("memory-history/index-before-migration-*.txt"))
    # New instance, including disabled instance, uses the same active set.
    await consolidator(project).maybe_run(client, ConversationManager(), "openai-compat")
    assert len(client.calls) == 1
    await item.set_enabled(False)
    assert len(active_records(root)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response,end", [
    ({"schema_version": 1, "noop": False, "groups": [{"sources": ["unknown"], "name": "x",
      "description": "x", "type": "project", "body": "x"}]}, "end_turn"),
    ('{"schema_version":', "end_turn"),
    ({"schema_version": 1, "noop": True, "groups": []}, "max_tokens"),
    ({"schema_version": 1, "noop": True, "groups": []}, None),
])
async def test_invalid_and_truncated_responses_keep_active_bodies(memory_project, response, end):
    project, root = memory_project
    original = MemoryStore.from_directory(root).migrate()
    item = consolidator(project)
    await item.maybe_run(ProposalClient(response, end=end), ConversationManager(), "openai-compat")
    assert item.status["state"] == "error"
    assert MemoryStore.from_directory(root).snapshot() == original
    assert not (root / STATE_FILE).exists()


@pytest.mark.asyncio
async def test_noop_records_only_successful_check(memory_project):
    project, root = memory_project
    client = ProposalClient({"schema_version": 1, "noop": True, "groups": []})
    item = consolidator(project)
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.status["state"] == "noop"
    assert len(active_records(root)) == 3
    assert json.loads((root / STATE_FILE).read_text())["last_success_ms"] == 1_800_000_000_000


@pytest.mark.asyncio
async def test_source_conflict_rejects_proposal(memory_project):
    project, root = memory_project
    async def change(_):
        (root / "alpha.md").write_text("Human revision")
    item = consolidator(project)
    await item.maybe_run(ProposalClient(before=change), ConversationManager(), "openai-compat")
    assert item.status["state"] == "conflict"
    assert len(active_records(root)) == 3
    assert (root / "alpha.md").read_text() == "Human revision"
    assert not (root / STATE_FILE).exists()


@pytest.mark.asyncio
async def test_concurrent_instances_use_real_lease_and_off_waits(memory_project):
    project, root = memory_project
    entered = asyncio.Event()
    async def wait(_):
        entered.set()
        await asyncio.Event().wait()
    client = ProposalClient(before=wait)
    first, second = consolidator(project), consolidator(project)
    task = asyncio.create_task(first.maybe_run(client, ConversationManager(), "openai-compat"))
    await entered.wait()
    await second.maybe_run(client, ConversationManager(), "openai-compat")
    assert second.status["state"] == "conflict" and len(client.calls) == 1
    await first.set_enabled(False)
    assert task.done() and task.cancelled()
    assert not (root / STATE_FILE).exists() and len(active_records(root)) == 3
    # Lease is released after cancellation.
    third = consolidator(project)
    await third.maybe_run(ProposalClient(), ConversationManager(), "openai-compat")
    assert third.status["state"] == "completed"


@pytest.mark.asyncio
async def test_timeout_leaves_no_success_or_background_task(memory_project):
    project, root = memory_project
    async def wait(_):
        await asyncio.Event().wait()
    item = consolidator(project, timeout=.01)
    await item.maybe_run(ProposalClient(before=wait), ConversationManager(), "openai-compat")
    assert item.status["state"] == "error" and item._task is None
    assert len(active_records(root)) == 3 and not (root / STATE_FILE).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["body", "index"])
async def test_publication_failure_preserves_old_active_set(memory_project, monkeypatch, stage):
    from nanocursor.memory import store as storage
    project, root = memory_project
    expected = MemoryStore.from_directory(root).migrate()
    write = storage._Directory.write
    def fail(self, name, content, **kwargs):
        if (stage == "body" and name.startswith("consolidated-")) or (stage == "index" and name == "MEMORY.md"):
            raise OSError("injected write failure")
        return write(self, name, content, **kwargs)
    monkeypatch.setattr(storage._Directory, "write", fail)
    item = consolidator(project)
    await item.maybe_run(ProposalClient(), ConversationManager(), "openai-compat")
    assert item.status["state"] == "error"
    assert MemoryStore.from_directory(root).snapshot() == expected
    assert not (root / STATE_FILE).exists()


@pytest.mark.asyncio
async def test_committed_index_recovers_failed_state_write_without_replaying(memory_project, monkeypatch):
    from nanocursor.memory import store as storage
    project, root = memory_project
    write = storage._Directory.write
    def fail_state(self, name, content, **kwargs):
        if name == STATE_FILE:
            raise OSError("disk state failure")
        return write(self, name, content, **kwargs)
    monkeypatch.setattr(storage._Directory, "write", fail_state)
    client = ProposalClient()
    item = consolidator(project)
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.status["state"] == "committed_error"
    assert len(active_records(root)) == 2
    monkeypatch.setattr(storage._Directory, "write", write)
    second = consolidator(project)
    await second.maybe_run(client, ConversationManager(), "openai-compat")
    assert len(client.calls) == 1 and (root / STATE_FILE).exists()


@pytest.mark.asyncio
async def test_damaged_index_and_symlink_never_enter_model(memory_project, tmp_path):
    project, root = memory_project
    (root / "MEMORY.md").write_text(INDEX_MARKER + "\n- [Missing](missing.md) — damaged\n")
    client = ProposalClient()
    item = consolidator(project)
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.status["state"] == "error" and not client.calls
    outside = tmp_path / "private.md"
    outside.write_text("private content")
    (root / "MEMORY.md").unlink()
    (root / "alpha.md").unlink()
    (root / "alpha.md").symlink_to(outside)
    await consolidator(project).maybe_run(client, ConversationManager(), "openai-compat")
    assert not client.calls and outside.read_text() == "private content"


def test_session_time_units_and_threshold(memory_project):
    project, _ = memory_project
    assert len(_list_sessions_since(str(project), 1_700_000_000_000)) == 4
    assert not _list_sessions_since(str(project), 1_800_000_000_000)


@pytest.mark.asyncio
async def test_bounded_batches_continue_without_new_session_gate(memory_project, monkeypatch):
    from nanocursor.memory import consolidation
    project, root = memory_project
    monkeypatch.setattr(consolidation, "MAX_RECORDS", 1)
    now = [1_800_000_000]
    client = ProposalClient({"schema_version": 1, "noop": True, "groups": []})
    item = MemoryConsolidator(str(project), enabled=True, clock=lambda: now[0])
    for count in range(3):
        await item.maybe_run(client, ConversationManager(), "openai-compat")
        now[0] += 601
        assert len(client.calls) == count + 1
    sent = [call[0]["memories"][0]["filename"] for call in client.calls]
    assert len(set(sent)) == 3
    assert not json.loads((root / STATE_FILE).read_text())["pending"]


@pytest.mark.asyncio
async def test_user_scope_never_receives_project_sessions(memory_project, monkeypatch, tmp_path):
    project, root = memory_project
    user = tmp_path / "home" / "memory"
    user.mkdir(parents=True)
    (user / "preference.md").write_text(render_memory("preference", "Concise", "user", "Be concise"))
    client = ProposalClient({"schema_version": 1, "noop": True, "groups": []})
    await consolidator(project).maybe_run(client, ConversationManager(), "openai-compat")
    user_payload = next(call[0] for call in client.calls if call[0]["scope"] == "user")
    assert user_payload["sessions"] == []
    assert all(record["filename"] == "preference.md" for record in user_payload["memories"])


@pytest.mark.asyncio
async def test_actual_gates_require_five_sessions_and_wait_after_success(memory_project):
    project, root = memory_project
    metas = sorted((project / ".nanocursor/sessions").glob("*.meta"))
    held = metas[0].read_bytes()
    metas[0].unlink()
    client = ProposalClient({"schema_version": 1, "noop": True, "groups": []})
    await consolidator(project).maybe_run(client, ConversationManager(), "openai-compat")
    assert not client.calls and INDEX_MARKER not in (root / "MEMORY.md").read_text()
    metas[0].write_bytes(held)
    item = consolidator(project)
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert len(client.calls) == 1
    for path in metas:
        data = json.loads(path.read_text())
        data["last_active"] = datetime.fromtimestamp(1_800_000_002, timezone.utc).isoformat()
        path.write_text(json.dumps(data))
    later = MemoryConsolidator(str(project), enabled=True, clock=lambda: 1_800_000_003)
    await later.maybe_run(client, ConversationManager(), "openai-compat")
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["duplicate", "cross_group", "cross_scope", "empty_body", "extra_key"])
async def test_proposal_rejects_ambiguous_references_and_scope_changes(memory_project, mutation):
    project, root = memory_project
    original = MemoryStore.from_directory(root).migrate()
    def proposal(payload):
        source = payload["memories"][0]["id"]
        group = {"sources": [source], "name": "Memory", "description": "Memory", "type": "project", "body": "A fact"}
        groups = [group]
        if mutation == "duplicate":
            group["sources"].append(source)
        elif mutation == "cross_group":
            groups.append(dict(group))
        elif mutation == "cross_scope":
            group["type"] = "user"
        elif mutation == "empty_body":
            group["body"] = "  "
        elif mutation == "extra_key":
            group["path"] = "/outside"
        return {"schema_version": 1, "noop": False, "groups": groups}
    item = consolidator(project)
    await item.maybe_run(ProposalClient(proposal), ConversationManager(), "openai-compat")
    assert item.status["state"] == "error"
    assert MemoryStore.from_directory(root).snapshot() == original


@pytest.mark.asyncio
async def test_more_session_batches_are_not_marked_consumed_without_request(memory_project):
    project, root = memory_project
    folder = project / ".nanocursor/sessions"
    sid = "session_20260926_120006_test"
    (folder / f"{sid}.meta").write_text(json.dumps({"id": sid,
        "last_active": datetime.fromtimestamp(1_700_000_006, timezone.utc).isoformat()}))
    now = [1_800_000_000]
    item = MemoryConsolidator(str(project), enabled=True, clock=lambda: now[0])
    client = ProposalClient({"schema_version": 1, "noop": True, "groups": []})
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    state = json.loads((root / STATE_FILE).read_text())
    assert state["pending"] and sid not in state["processed_sessions"]
    now[0] += 601
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert len(client.calls) == 2
    assert [entry["id"] for entry in client.calls[1][0]["sessions"]] == [sid]
    assert not json.loads((root / STATE_FILE).read_text())["pending"]


@pytest.mark.asyncio
async def test_migration_failure_does_not_publish_partial_version_marker(memory_project, monkeypatch):
    from nanocursor.memory import store as storage
    project, root = memory_project
    original = (root / "MEMORY.md").read_bytes()
    write = storage._Directory.write
    def fail(self, name, content, **kwargs):
        if name == "MEMORY.md":
            raise OSError("migration failure")
        return write(self, name, content, **kwargs)
    monkeypatch.setattr(storage._Directory, "write", fail)
    item = consolidator(project)
    client = ProposalClient()
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert not client.calls and item.status["state"] == "error"
    assert (root / "MEMORY.md").read_bytes() == original
    assert len(active_records(root)) == 3


@pytest.mark.asyncio
async def test_lost_migration_marker_does_not_reactivate_retired_bodies(memory_project):
    project, root = memory_project
    await consolidator(project).maybe_run(ProposalClient(), ConversationManager(), "openai-compat")
    (root / "MEMORY.md").write_text("# Accidentally replaced index\n")
    item = consolidator(project)
    client = ProposalClient()
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.status["state"] == "error" and not client.calls
    assert not scan_memory_files(root, "project")


@pytest.mark.asyncio
async def test_post_replace_durability_failure_reports_published(memory_project, monkeypatch):
    import os
    import stat
    project, root = memory_project
    MemoryStore.from_directory(root).migrate()
    original = os.fsync
    directories = [0]
    def fail_after_index_replace(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directories[0] += 1
            if directories[0] == 2:  # new body, then active index
                raise OSError("directory sync failure")
        return original(fd)
    monkeypatch.setattr(os, "fsync", fail_after_index_replace)
    item = consolidator(project)
    await item.maybe_run(ProposalClient(), ConversationManager(), "openai-compat")
    assert item.status["state"] == "committed_error"
    assert item.publication_revision == 1
    assert item.status["committed_scopes"] == ["project"]
    assert len(active_records(root)) == 2
    assert (root / "alpha.md").exists() and (root / "beta.md").exists()


@pytest.mark.asyncio
async def test_cancel_timeout_is_bounded_and_late_model_return_cannot_publish(memory_project):
    from nanocursor.memory.store import MemoryStorageError
    project, root = memory_project
    entered, release = asyncio.Event(), asyncio.Event()
    cancellations = []
    async def resistant(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations.append(True)
            await release.wait()
    item = consolidator(project, cancel_timeout=.01)
    task = asyncio.create_task(item.maybe_run(ProposalClient(before=resistant), ConversationManager(), "openai-compat"))
    await entered.wait()
    try:
        with pytest.raises(MemoryStorageError, match="尚未停止"):
            await item.cancel_and_wait()
        with pytest.raises(MemoryStorageError, match="尚未停止"):
            await item.cancel_and_wait()
        assert len(cancellations) == 1 and not task.done()
    finally:
        release.set()
        await task
    assert len(active_records(root)) == 3 and not (root / STATE_FILE).exists()
    assert item.status["state"] == "cancelled"


@pytest.mark.asyncio
async def test_later_scope_cannot_hide_earlier_failure(memory_project):
    project, root = memory_project
    user = project.parent / "home" / "memory"
    user.mkdir(parents=True)
    (user / "preference.md").write_text(render_memory("preference", "Concise", "user", "Be concise"))
    client = ProposalClient(lambda payload: "invalid" if payload["scope"] == "project" else
                            {"schema_version": 1, "noop": True, "groups": []})
    item = consolidator(project)
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.status["state"] == "error" and len(client.calls) == 1
    assert not (user / STATE_FILE).exists()


@pytest.mark.asyncio
async def test_publication_revision_counts_commits_not_throttled_checks_or_noops(memory_project):
    project, root = memory_project
    now = [1_800_000_000]
    item = MemoryConsolidator(str(project), enabled=True, min_hours=0, min_sessions=0,
                              clock=lambda: now[0])
    client = ProposalClient()
    assert item.publication_revision == 0
    with pytest.raises(AttributeError):
        item.publication_revision = 10
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.publication_revision == 1 and len(client.calls) == 1
    # The last useful status may still mention a committed scope. A throttled
    # check is not a new publication and must not repeatedly refresh the TUI.
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.publication_revision == 1 and len(client.calls) == 1
    now[0] += 601
    client.response = {"schema_version": 1, "noop": True, "groups": []}
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.publication_revision == 1 and len(client.calls) == 2
    assert item.status["state"] == "noop"
    await item.set_enabled(False)
    now[0] += 601
    await item.maybe_run(client, ConversationManager(), "openai-compat")
    assert item.publication_revision == 1 and len(client.calls) == 2
