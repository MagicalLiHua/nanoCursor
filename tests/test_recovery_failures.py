"""Acceptance A11/A13/A14: actual damaged storage and process boundaries."""
from __future__ import annotations

import os
import select
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from nanocursor.conversation import Message
from nanocursor.memory.session import SessionManager
from nanocursor.permissions.approval_context import AuthorizationContext
from nanocursor.recovery import (
    RecoveryRequired, RecoveryRuntime,
    RecoveryStorageError, RecoveryStore, WorkspaceBusy,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "home"))


def project(tmp_path, name="project"):
    directory = tmp_path / name
    directory.mkdir()
    return directory


def run_crashing_owner(directory: Path, state: Path, *, process_hint: int | None = None):
    """Publish a real local side effect, then leave its intent without a result."""
    script = """
import os,sys
from pathlib import Path
from nanocursor.recovery import RecoveryRuntime,RecoveryStore
r=RecoveryRuntime.acquire(sys.argv[1],store=RecoveryStore(sys.argv[2]))
r.begin_run()
operation=r.begin_operation('tool','Bash',{'command':'append execution witness'})
if sys.argv[3] != 'None':
    r.store.put_metadata('process',operation,{'pid':int(sys.argv[3]),'pgid':int(sys.argv[3]),'owner_token':r.owner_token,'started_at':1.0})
with (Path(sys.argv[1])/'execution-witness').open('a') as stream:
    stream.write('executed once\\n')
    stream.flush()
    os.fsync(stream.fileno())
os._exit(77)
"""
    result = subprocess.run([sys.executable, "-c", script, str(directory), str(state), str(process_hint)],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 77, result.stderr


@pytest.mark.parametrize("damage", ["broken_header", "unsupported_schema"])
def test_a13_existing_bad_database_stops_dispatch_and_preserves_bytes(tmp_path, damage):
    directory = project(tmp_path)
    state = tmp_path / "home" / "recovery"
    state.mkdir(parents=True)
    database = state / "recovery.sqlite3"
    if damage == "broken_header":
        store = RecoveryStore(state)
        store.put_metadata("original_evidence", "receipt", {"result": "must remain available"})
        store.close()
        original = database.read_bytes()
        database.write_bytes(b"BROKEN SQLITE!!!\x00" + original[16:])
    else:
        # A future application's database includes evidence our schema cannot
        # understand; initialization must not rebuild or erase it.
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE recovery_schema(version INTEGER NOT NULL)")
            connection.execute("INSERT INTO recovery_schema VALUES(999)")
            connection.execute("CREATE TABLE future_evidence(receipt TEXT NOT NULL)")
            connection.execute("INSERT INTO future_evidence VALUES('must not be discarded')")
    evidence = database.read_bytes()
    inode = database.stat().st_ino
    dispatched = tmp_path / "unexpected-effect"
    with pytest.raises(RecoveryStorageError):
        runtime = RecoveryRuntime.acquire(directory)
        try:
            runtime.begin_operation("tool", "Bash", {"command": "write unexpected effect"})
            dispatched.write_text("should never execute")
        finally:
            runtime.close()
    assert not dispatched.exists()
    assert database.stat().st_ino == inode
    assert database.read_bytes() == evidence
    if damage == "unsupported_schema":
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT receipt FROM future_evidence").fetchone() == ("must not be discarded",)
            assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == [
                ("future_evidence",), ("recovery_schema",),
            ]


def test_a14_two_processes_cannot_own_one_workspace(tmp_path):
    directory = project(tmp_path)
    state = tmp_path / "home" / "recovery"
    script = """
import sys
from nanocursor.recovery import RecoveryRuntime,RecoveryStore
r=RecoveryRuntime.acquire(sys.argv[1],store=RecoveryStore(sys.argv[2]))
print(r.owner_token,flush=True)
sys.stdin.read(1)
r.close()
r.store.close()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(directory), str(state)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    store = None
    try:
        ready, _, _ = select.select([child.stdout], [], [], 10)
        assert ready, "Owner subprocess did not become ready"
        owner_token = child.stdout.readline().strip()
        assert owner_token.startswith("owner_"), child.stderr.read() if child.poll() is not None else owner_token
        store = RecoveryStore(state)
        with pytest.raises(WorkspaceBusy):
            RecoveryRuntime.acquire(directory, store=store)
        row = store.rows("SELECT state,pid FROM owners WHERE owner_token=?", (owner_token,))[0]
        assert row == {"state": "active", "pid": child.pid}
        _, errors = child.communicate("\n", timeout=10)
        assert child.returncode == 0, errors
        replacement = RecoveryRuntime.acquire(directory, store=store)
        replacement.ensure_ready()
        replacement.close()
    finally:
        if child.poll() is None:
            child.terminate()  # Only this test's still-owned live child.
            child.communicate(timeout=10)
        if store is not None:
            store.close()


def test_a14_other_checkout_and_new_session_cannot_bypass_unknown_gate(tmp_path):
    directory = project(tmp_path)
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    subprocess.run(["git", "-C", str(directory), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "initial"], check=True)
    checkout = tmp_path / "other-checkout"
    subprocess.run(["git", "-C", str(directory), "worktree", "add", "-qb", "other", str(checkout)], check=True)
    store = RecoveryStore(tmp_path / "home" / "recovery")
    sibling = RecoveryRuntime.acquire(checkout, store=store)
    try:
        # This checkout already owns its lock when a different checkout crashes.
        # The next dispatch must refresh the project gate, not trust startup.
        run_crashing_owner(directory, store.root)
        session = SessionManager(str(checkout), recovery=sibling).create()
        try:
            with pytest.raises(RecoveryRequired):
                sibling.begin_run(session.session_id)
            with pytest.raises(RecoveryRequired):
                sibling.begin_operation("tool", "Bash", {"command": "second effect"})
            pending = sibling.pending()
            assert len(pending) == 1 and pending[0]["cwd"] == str(directory.resolve())
            assert pending[0]["project_id"] == sibling.project_id
            assert pending[0]["workspace_id"] != sibling.workspace_id
            assert (directory / "execution-witness").read_text() == "executed once\n"
            assert len(store.rows("SELECT * FROM operations")) == 1
        finally:
            session.close()
        independent = RecoveryRuntime.acquire(project(tmp_path, "independent"), store=store)
        try:
            operation = independent.begin_operation("tool", "ReadFile", {})
            independent.finish_operation(operation, "independent project remains usable")
        finally:
            independent.close()
    finally:
        sibling.close()
        store.close()


def test_a11_recovery_does_not_signal_historical_pid_or_replay(tmp_path, monkeypatch):
    directory = project(tmp_path)
    state = tmp_path / "home" / "recovery"
    # Deliberately use this process's PID as stale evidence. Spies below ensure
    # it is never treated as a capability to signal a process after restart.
    old_pid = os.getpid()
    run_crashing_owner(directory, state, process_hint=old_pid)
    calls = []
    def forbid_signal(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Recovery must not signal archived process IDs")
    monkeypatch.setattr(os, "kill", forbid_signal)
    monkeypatch.setattr(os, "killpg", forbid_signal)
    store = RecoveryStore(state)
    runtime = RecoveryRuntime.acquire(directory, store=store)
    try:
        pending = runtime.pending()
        assert len(pending) == 1
        operation = pending[0]["operation_id"]
        assert store.get_metadata("process", operation)["pid"] == old_pid
        with pytest.raises(RecoveryRequired):
            runtime.ensure_ready()
        assert not calls
        assert (directory / "execution-witness").read_text() == "executed once\n"
        assert len(store.rows("SELECT * FROM operations")) == 1
    finally:
        runtime.close()
        store.close()


def test_rewind_boundary_revokes_old_authorization_even_before_clear_record(tmp_path):
    directory = project(tmp_path)
    runtime = RecoveryRuntime.acquire(directory)
    manager = SessionManager(str(directory), recovery=runtime)
    session = manager.create()
    try:
        old = AuthorizationContext()
        old.add("Old task authorization must not survive rewind")
        session.save_approval_context(old)
        assert session.load_approval_context().complete
        # The process can stop here, before the UI's separate cleared-context
        # record. The already-durable boundary must itself revoke old context.
        session.reset_history([Message("user", "rewound conversation")], record_id="rewind-window")
        resumed = manager.resume(session.session_id)
        try:
            recovered = resumed.session.load_approval_context()
            assert not recovered.complete and not recovered.records
            fresh = AuthorizationContext()
            fresh.add("New explicit user authorization")
            resumed.session.save_approval_context(fresh)
            assert resumed.session.load_approval_context().to_dict() == fresh.to_dict()
        finally:
            resumed.session.close()
    finally:
        session.close()
        runtime.close()
        runtime.store.close()
