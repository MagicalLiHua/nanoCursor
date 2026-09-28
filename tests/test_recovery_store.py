"""Fault-boundary tests for durable facts, ownership and transcript projection."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nanocursor.conversation import Message, ToolResultBlock, ToolUseBlock
from nanocursor.memory.session import SessionManager
from nanocursor.recovery import (RecoveryIntegrityError, RecoveryRequired,
                                RecoveryRuntime, RecoveryStorageError,
                                RecoveryStore, WorkspaceBusy)


@pytest.fixture
def recovery(tmp_path):
    work = tmp_path / "project"
    work.mkdir()
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(work, store=store)
    yield runtime, work
    runtime.close()
    store.close()


def session_for(runtime, work):
    manager = SessionManager(str(work), recovery=runtime)
    return manager, manager.create()


def test_sqlite_durability_and_private_content(recovery):
    runtime, _ = recovery
    store = runtime.store
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert store.connection.execute("PRAGMA synchronous").fetchone()[0] == 3
    assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    digest = store.put_blob(b"uncommitted user content")
    assert store.get_blob(digest) == b"uncommitted user content"
    assert store.blob_dir.joinpath(digest).stat().st_mode & 0o777 == 0o600
    store.blob_dir.joinpath(digest).write_bytes(b"changed")
    with pytest.raises(RecoveryIntegrityError, match="checksum"):
        store.get_blob(digest)


def test_workspace_has_exclusive_owner_and_child_path_cannot_bypass(recovery):
    runtime, work = recovery
    with pytest.raises(WorkspaceBusy):
        RecoveryRuntime.acquire(work, store=runtime.store)
    nested = work / "nested"
    nested.mkdir()
    assert runtime.store.register_workspace(nested).workspace_id == runtime.workspace_id
    with pytest.raises(WorkspaceBusy):
        RecoveryRuntime.acquire(nested, store=runtime.store)


def test_unknown_acknowledgement_preserves_fact_and_does_not_replay(recovery):
    runtime, _ = recovery
    operation = runtime.begin_operation("tool", "MCP.transfer", {"amount": 10})
    with pytest.raises(RecoveryRequired, match="live execution"):
        runtime.acknowledge(operation, "accept")
    runtime.mark_unknown(operation, "network disappeared after send")
    with pytest.raises(RecoveryRequired):
        runtime.begin_operation("tool", "ReadFile", {})
    runtime.acknowledge(operation, "I inspected the service and accept the current state")
    runtime.ensure_ready()
    row = runtime.store.rows("SELECT * FROM operations WHERE operation_id=?", (operation,))[0]
    assert row["state"] == "outcome_unknown"
    assert row["result"] is None
    assert len(runtime.store.rows("SELECT * FROM resolutions")) == 1
    assert len(runtime.store.rows("SELECT * FROM operations")) == 1


def test_crashed_owner_intent_is_unknown(tmp_path):
    work = tmp_path / "project"
    work.mkdir()
    state = tmp_path / "state"
    code = """
import os,sys
from nanocursor.recovery import RecoveryRuntime, RecoveryStore
r=RecoveryRuntime.acquire(sys.argv[1],store=RecoveryStore(sys.argv[2]))
r.begin_run()
r.begin_operation('tool','Bash',{'command':'example'})
os._exit(77)
"""
    result = subprocess.run([sys.executable, "-c", code, str(work), str(state)], check=False)
    assert result.returncode == 77
    store = RecoveryStore(state)
    runtime = RecoveryRuntime.acquire(work, store=store)
    try:
        assert len(runtime.pending()) == 1
        assert runtime.pending()[0]["state"] == "outcome_unknown"
        assert runtime.store.rows("SELECT state FROM runs")[0]["state"] == "interrupted"
    finally:
        runtime.close()
        store.close()


def test_known_result_recovers_without_reexecuting_or_trimming_batch(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    runtime.begin_run()
    runtime.persist_message(Message("user", "run two calls"))
    runtime.persist_message(Message("assistant", "", tool_uses=[
        ToolUseBlock("done", "Bash", {"command": "first"}),
        ToolUseBlock("lost", "Bash", {"command": "second"}),
    ]))
    done = runtime.begin_operation("tool", "Bash", {}, tool_call_id="done")
    runtime.finish_operation(done, "historical output")
    lost = runtime.begin_operation("tool", "Bash", {}, tool_call_id="lost")
    runtime.mark_unknown(lost, "disconnected")
    session.close()
    resumed = manager.resume(session.session_id)
    try:
        assert len(resumed.messages) == 3
        results = {r.tool_use_id: r for r in resumed.messages[-1].tool_results}
        assert results["done"].content == "historical output"
        assert "unknown" in results["lost"].content
        assert len(runtime.pending()) == 1
    finally:
        resumed.session.close()


def test_same_message_object_persisted_once_but_identical_new_message_kept(recovery):
    runtime, work = recovery
    _, session = session_for(runtime, work)
    message = Message("user", "repeat")
    runtime.persist_message(message)
    session.append(message)
    session.append(Message("user", "repeat"))
    assert len(runtime.store.projection_records(runtime.session_key)) == 2
    session.close()


def test_projection_crash_after_sqlite_commit_recovers_full_payload(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    def fail(phase):
        if phase == "outbox_committed":
            raise RuntimeError("simulated process loss")
    runtime.store.fault_hook = fail
    with pytest.raises(RuntimeError, match="process loss"):
        session.append(Message("user", "durable but not projected"))
    runtime.store.fault_hook = None
    session.close()
    resumed = manager.resume(session.session_id)
    assert resumed.messages[0].content == "durable but not projected"
    resumed.session.close()
    again = manager.resume(session.session_id)
    assert len(again.messages) == 1
    again.session.close()


def test_projection_confirmation_loss_does_not_duplicate_record(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    def fail(phase):
        if phase == "projection_synced":
            raise RuntimeError("confirmation not committed")
    runtime.store.fault_hook = fail
    with pytest.raises(RuntimeError):
        session.append(Message("user", "once"))
    runtime.store.fault_hook = None
    session.close()
    resumed = manager.resume(session.session_id)
    assert [m.content for m in resumed.messages] == ["once"]
    resumed.session.close()


def test_projection_checks_confirmed_content_and_stops_on_conflict(recovery):
    runtime, work = recovery
    _, session = session_for(runtime, work)
    session.append(Message("user", "original"))
    path = session._sessions_dir / f"{session.session_id}.jsonl"
    data = json.loads(path.read_text())
    data["content"] = "tampered"
    path.write_text(json.dumps(data) + "\n")
    with pytest.raises(RecoveryIntegrityError, match="disagree"):
        runtime.store.project(runtime.session_key, path)
    session.close()


def test_corrupt_tail_is_preserved_separately_and_middle_corruption_rejected(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    session.append(Message("user", "keep"))
    path = session._sessions_dir / f"{session.session_id}.jsonl"
    session.close()
    path.write_bytes(path.read_bytes() + b'{"partial":')
    original = path.read_bytes()
    resumed = manager.resume(session.session_id)
    assert resumed.messages[0].content == "keep"
    assert list(path.parent.glob(path.name + ".corrupt-*"))[0].read_bytes() == original
    resumed.session.close()
    path.write_bytes(b'{bad}\n' + path.read_bytes())
    with pytest.raises(RecoveryIntegrityError, match="middle corruption"):
        manager.resume(session.session_id)


def test_rewind_idempotent_generation_and_old_results_not_reinjected(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    runtime.begin_run()
    runtime.persist_message(Message("assistant", "", tool_uses=[ToolUseBlock("old", "Bash", {})]))
    op = runtime.begin_operation("tool", "Bash", {}, tool_call_id="old")
    runtime.finish_operation(op, "old outcome")
    session.reset_history([Message("user", "new branch")], record_id="rewind_1")
    assert runtime.generation == 1
    session.reset_history([Message("user", "new branch")], record_id="rewind_1")
    assert runtime.generation == 1
    session.close()
    resumed = manager.resume(session.session_id)
    assert [m.content for m in resumed.messages] == ["new branch"]
    assert len(runtime.store.rows("SELECT * FROM operations")) == 1
    resumed.session.close()


def test_reused_provider_tool_ids_are_distinct_model_turns(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    runtime.begin_run()
    for output in ["first", "second"]:
        runtime.persist_message(Message("assistant", "", tool_uses=[ToolUseBlock("reused", "Bash", {})]))
        op = runtime.begin_operation("tool", "Bash", {}, tool_call_id="reused")
        runtime.finish_operation(op, output)
        runtime.persist_message(Message("user", "", tool_results=[ToolResultBlock("reused", output)]))
    session.close()
    resumed = manager.resume(session.session_id)
    results = [r.content for m in resumed.messages for r in m.tool_results]
    assert results == ["first", "second"]
    resumed.session.close()


def test_storage_failure_is_sticky_and_stops_next_dispatch(recovery, monkeypatch):
    runtime, _ = recovery
    def fail(phase):
        if phase == "before_commit":
            raise OSError("disk full")
    runtime.store.fault_hook = fail
    with pytest.raises(RecoveryStorageError):
        runtime.begin_operation("tool", "Bash", {})
    runtime.store.fault_hook = None
    with pytest.raises(RecoveryStorageError, match="previously failed"):
        runtime.begin_operation("tool", "Bash", {})
    runtime.store.failed = False  # release fixture ownership after simulated disk repair


def test_approval_lifecycle_and_unstarted_result_are_honest(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    runtime.begin_run()
    runtime.persist_message(Message("assistant", "", tool_uses=[ToolUseBlock("deny", "Bash", {})]))
    op = runtime.begin_operation("tool", "Bash", {}, tool_call_id="deny", state="planned")
    runtime.wait_for_approval(op)
    runtime.finish_not_started(op, "User denied this command")
    assert runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (op,))[0]["state"] == "interrupted"
    session.close()
    restored = manager.resume(session.session_id)
    assert restored.messages[-1].tool_results[0].content == "User denied this command"
    assert not runtime.pending()
    restored.session.close()


def test_intent_cannot_be_relabelled_not_started(recovery):
    runtime, _ = recovery
    op = runtime.begin_operation("tool", "MCP.write", {})
    with pytest.raises(RecoveryIntegrityError):
        runtime.finish_not_started(op, "probably not run")


def test_reading_resumed_view_does_not_replace_live_session(recovery):
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    runtime.persist_message(Message("user", "original"))
    resumed = manager.resume(session.session_id)
    resumed.session.close()
    assert runtime.session is session
    runtime.persist_message(Message("assistant", "still live"))
    assert [p["content"] for p in runtime.store.projection_records(runtime.session_key)] == ["original", "still live"]
    session.close()


def test_background_notification_survives_result_projection_gap(recovery):
    from nanocursor.recovery.store import now
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    operation = runtime.begin_operation("background", "Task", {})
    notification = {"session_key": runtime.session_key, "record": {
        "type": "user", "content": "Historical background result", "timestamp": now(),
        "record_id": "notification_" + operation, "generation": runtime.generation,
        "recovery_source": {"operation_id": operation, "kind": "background"},
    }}
    runtime.finish_operation(operation, {"status": "done", "notification": notification})
    session.close()
    resumed = manager.resume(session.session_id)
    assert resumed.messages[0].content == "Historical background result"
    resumed.session.close()
    again = manager.resume(session.session_id)
    assert len(again.messages) == 1
    again.session.close()


@pytest.mark.asyncio
async def test_concurrent_child_runs_keep_distinct_operation_lineage(recovery):
    import asyncio
    runtime, _ = recovery
    ready = asyncio.Event()
    async def child(name):
        with runtime.activate():
            run = runtime.begin_run(source="subagent")
            runtime.persist_child_message(Message("assistant", name), name)
            turn = runtime.model_turn_id
            ready.set()
            await asyncio.sleep(0)
            operation = runtime.begin_operation("tool", "ReadFile", {})
            runtime.finish_operation(operation, name)
            runtime.end_run(run)
            return run, turn, operation
    results = await asyncio.gather(child("a"), child("b"))
    assert results[0][0] != results[1][0]
    for run, turn, operation in results:
        row = runtime.store.rows("SELECT * FROM operations WHERE operation_id=?", (operation,))[0]
        assert row["run_id"] == run and row["model_turn_id"] == turn
    assert runtime.run_id is None


def test_compacted_old_result_is_not_reinjected(recovery):
    from nanocursor.memory.session import make_compact_boundary
    runtime, work = recovery
    manager, session = session_for(runtime, work)
    runtime.begin_run()
    runtime.persist_message(Message("assistant", "", tool_uses=[ToolUseBlock("old", "Bash", {})]))
    operation = runtime.begin_operation("tool", "Bash", {}, tool_call_id="old")
    runtime.finish_operation(operation, "old result")
    session.append_record(make_compact_boundary("summarized", [Message("user", "retained")]))
    session.close()
    resumed = manager.resume(session.session_id)
    assert not any(m.tool_results for m in resumed.messages)
    assert "summarized" in resumed.messages[0].content
    resumed.session.close()


def test_worktree_session_lease_survives_switch_new_session_and_return(tmp_path):
    from nanocursor.recovery.runtime import _FileLock
    import hashlib
    work = tmp_path / "repo"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "initial"], check=True)
    checkout = tmp_path / "other"
    subprocess.run(["git", "-C", str(work), "worktree", "add", "-qb", "other", str(checkout)], check=True)
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(work, store=store)
    original_manager = SessionManager(str(work), recovery=runtime)
    first = original_manager.create()
    first_path = first._sessions_dir / f"{first.session_id}.jsonl"
    try:
        active = runtime.switch(checkout)
        assert active.session is first
        manager = SessionManager(str(work), recovery=active)
        second = manager.create()
        assert runtime.session is second and active.session is second
        old_lease = _FileLock(store.lock_dir / ("session-" + hashlib.sha256(str(first_path.absolute()).encode()).hexdigest() + ".lock"))
        old_lease.acquire()
        old_lease.release()
        returned = active.switch(work)
        assert returned is runtime and returned.session is second
        runtime.ensure_workspace_idle(checkout)
        runtime.persist_message(Message("user", "after switching"))
        second.close()
    finally:
        first.close()
        runtime.close()
    again = RecoveryRuntime.acquire(work, store=store)
    try:
        result = SessionManager(str(work), recovery=again).resume(second.session_id)
        assert result.messages[-1].content == "after switching"
        result.session.close()
    finally:
        again.close()
        store.close()


def test_moving_registered_workspace_requires_explicit_reassociation(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    store = RecoveryStore(tmp_path / "state")
    store.register_workspace(original)
    moved = tmp_path / "moved"
    original.rename(moved)
    try:
        with pytest.raises(RecoveryIntegrityError, match="moved"):
            store.register_workspace(moved)
    finally:
        store.close()


def test_non_git_ancestor_cannot_bypass_registered_child_lock(tmp_path):
    parent = tmp_path / "project"
    child = parent / "child"
    child.mkdir(parents=True)
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(child, store=store)
    try:
        with pytest.raises(RecoveryIntegrityError, match="contains a registered workspace"):
            RecoveryRuntime.acquire(parent, store=store)
    finally:
        runtime.close()
        store.close()


@pytest.mark.asyncio
async def test_direct_switch_close_does_not_leak_closed_runtime_to_new_agent(tmp_path):
    from nanocursor.recovery import current_runtime
    from test_execution_boundaries import agent, drive
    first, second, third = [tmp_path / name for name in ("one", "two", "three")]
    for path in (first, second, third):
        path.mkdir()
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(first, store=store)
    switched = runtime.switch(second)
    assert current_runtime() is switched
    runtime.close()
    store.close()
    assert current_runtime() is None
    fresh = agent(third)
    await drive(fresh)
    assert fresh.recovery is None


def test_normal_unknown_result_projection_retains_structured_provenance(recovery):
    runtime, work = recovery
    _, session = session_for(runtime, work)
    runtime.persist_message(Message("assistant", "", tool_uses=[ToolUseBlock("unknown", "MCP.update", {})]))
    operation = runtime.begin_operation("tool", "MCP.update", {}, tool_call_id="unknown")
    runtime.mark_unknown(operation, "socket closed")
    runtime.persist_message(Message("user", "", tool_results=[ToolResultBlock("unknown", "Connection closed; outcome unconfirmed", True)]))
    record = runtime.store.projection_records(runtime.session_key)[-1]
    assert record["record_id"] == "result_" + operation
    assert record["recovery_source"]["state"] == "outcome_unknown"
    assert record["recovery_source"]["operation_id"] == operation
    assert record["content"].startswith("[Host recovery observation:")
    assert record["content"].endswith("Connection closed; outcome unconfirmed")
    session.close()


def test_close_after_known_storage_failure_releases_without_masking_it(recovery):
    runtime, _ = recovery
    runtime.store.failed = True
    runtime.close()
    assert runtime._closed and runtime._lock.fd is None


def test_one_child_close_failure_releases_every_runtime_lease(tmp_path):
    paths = [tmp_path / name for name in ("one", "two", "three")]
    for path in paths:
        path.mkdir()
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(paths[0], store=store)
    children = [runtime.child(path) for path in paths[1:]]
    def fault(phase):
        if phase == "before_commit":
            raise OSError("close-time persistence failure")
    store.fault_hook = fault
    with pytest.raises(RecoveryStorageError):
        runtime.close()
    assert all(item._closed and item._lock.fd is None for item in [runtime, *children])
    store.close()


def test_live_workspace_path_replacement_is_rejected_before_dispatch(recovery):
    runtime, work = recovery
    original = work.with_name("moved-original")
    work.rename(original)
    work.mkdir()
    with pytest.raises(RecoveryIntegrityError, match="moved or replaced"):
        runtime.begin_operation("tool", "Bash", {"command": "touch should-not-exist"})
    assert not runtime.store.rows("SELECT * FROM operations")
    assert not (work / "should-not-exist").exists()


def test_terminal_observation_can_be_recorded_after_explicit_workspace_removal(recovery):
    runtime, work = recovery
    operation = runtime.begin_operation("command", "remove_workspace", {})
    runtime.store.mark_workspace_removed(runtime.workspace_id)
    work.rmdir()
    runtime.finish_operation(operation, {"removed": True})
    assert runtime.store.rows("SELECT state FROM operations WHERE operation_id=?", (operation,))[0]["state"] == "completed"
