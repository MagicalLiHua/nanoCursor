"""Execution ownership and deterministic recovery; never replays a tool."""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import RecoveryIntegrityError, RecoveryRequired, RecoveryStorageError, WorkspaceBusy
from .store import RecoveryStore, canonical_json, identifier, now

_current = contextvars.ContextVar("nanocursor_recovery_runtime", default=None)
_parent = contextvars.ContextVar("nanocursor_parent_operation", default=None)


def current_runtime():
    return _current.get()


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def acquire(self) -> None:
        try:
            import fcntl
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self.release()
                raise WorkspaceBusy("Another nanoCursor process owns this workspace or session") from exc
        except (ImportError, OSError) as exc:
            self.release()
            raise RecoveryStorageError("Cannot establish an exclusive recovery lock") from exc

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class RecoveryRuntime:
    def __init__(self, store: RecoveryStore, workspace, lock: _FileLock):
        self.store = store
        self.workspace = workspace
        self.owner_token = identifier("owner")
        self._lock = lock
        self._session_lock = None
        self._session_lock_key = None
        self._closed = False
        self.session = None
        self.session_id = None
        self.session_key = None
        self.generation = 0
        self._run_var = contextvars.ContextVar("recovery_run_" + self.owner_token, default=None)
        self._turn_var = contextvars.ContextVar("recovery_turn_" + self.owner_token, default=None)
        self.file_history = None
        self._children = []
        self._session_host = self
        self._tree_root = self
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO owners VALUES(?,?,?,?,?)", (self.owner_token, workspace.workspace_id, os.getpid(), now(), "active"))

    @classmethod
    def acquire(cls, work_dir: str | Path, session=None, store: RecoveryStore | None = None):
        store = store or RecoveryStore()
        workspace = store.register_workspace(work_dir)
        lock = _FileLock(store.lock_dir / (workspace.workspace_id + ".lock"))
        lock.acquire()
        try:
            runtime = cls(store, workspace, lock)
            runtime.reconcile_owners()
            if session is not None:
                runtime.bind_session(session)
            return runtime
        except BaseException:
            lock.release()
            raise

    @property
    def run_id(self):
        return self._run_var.get()

    @run_id.setter
    def run_id(self, value):
        self._run_var.set(value)

    @property
    def model_turn_id(self):
        return self._turn_var.get()

    @model_turn_id.setter
    def model_turn_id(self, value):
        self._turn_var.set(value)

    @property
    def current_operation_id(self):
        return _parent.get()

    @property
    def workspace_id(self):
        return self.workspace.workspace_id

    @property
    def project_id(self):
        return self.workspace.project_id

    @contextmanager
    def activate(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    @contextmanager
    def operation_context(self, operation_id: str):
        token = _parent.set(operation_id)
        try:
            yield operation_id
        finally:
            _parent.reset(token)

    def child(self, work_dir: str | Path | None = None):
        if work_dir is None:
            return self
        identity = self.store.register_workspace(work_dir)
        root = self._tree_root
        for existing in [root, *root._children]:
            if existing.workspace_id == identity.workspace_id and not existing._closed:
                return existing
        child = type(self).acquire(work_dir, store=self.store)
        child._tree_root = root
        root._children.append(child)
        return child

    def switch(self, work_dir: str | Path):
        target = self.child(work_dir)
        target.ensure_ready()
        if target is self:
            return self
        target.run_id = self.run_id
        target.model_turn_id = self.model_turn_id
        if self.session is not None:
            target.bind_session(self.session, shared_with=self)
        _current.set(target)
        return target

    def _require_owner(self, conn, *, dispatch: bool = False) -> None:
        if self._closed or self._lock.fd is None:
            raise WorkspaceBusy("Workspace ownership was released")
        row = conn.execute("SELECT o.state,w.root,w.fingerprint,w.git_common_dir FROM owners o JOIN workspaces w USING(workspace_id) WHERE o.owner_token=?", (self.owner_token,)).fetchone()
        if row is None or row["state"] != "active":
            raise WorkspaceBusy("Workspace ownership is no longer active")
        if dispatch:
            self._validate_workspace_identity(row)

    def _validate_workspace_identity(self, row) -> None:
        """Cheap filesystem validation immediately at each dispatch barrier.

        Terminal observations remain writable after an explicitly removed
        checkout. Only granting new execution revalidates the live path.
        """
        root = Path(self.workspace.root)
        try:
            info = root.stat()
            if (root.resolve(strict=True) != root or str(root) != row["root"]
                    or f"{info.st_dev}:{info.st_ino}" != row["fingerprint"]):
                raise RecoveryIntegrityError("Workspace path was moved or replaced; execution is stopped")
            marker = root / ".git"
            current_common = None
            if marker.is_dir():
                gitdir = marker.resolve(strict=True)
            elif marker.is_file():
                # Linked checkouts use a small gitdir pointer file.
                pointer = marker.read_text(encoding="utf-8")
                if len(pointer) > 8192 or not pointer.startswith("gitdir: "):
                    raise RecoveryIntegrityError("Workspace Git identity is invalid")
                gitdir = (root / pointer[8:].strip()).resolve(strict=True)
            else:
                gitdir = None
            if gitdir is not None:
                common_pointer = gitdir / "commondir"
                if common_pointer.exists():
                    relative = common_pointer.read_text(encoding="utf-8")
                    if len(relative) > 8192:
                        raise RecoveryIntegrityError("Workspace common Git identity is invalid")
                    common_path = (gitdir / relative.strip()).resolve(strict=True)
                else:
                    common_path = gitdir
                current_common = str(common_path)
                common_info = common_path.stat()
                source = f"git:{current_common}:{common_info.st_dev}:{common_info.st_ino}"
                project_id = "project_" + hashlib.sha256(source.encode()).hexdigest()[:32]
                if project_id != self.project_id:
                    raise RecoveryIntegrityError("Workspace Git directory was replaced; execution is stopped")
            if current_common != row["git_common_dir"]:
                raise RecoveryIntegrityError("Workspace Git association changed; execution is stopped")
        except (OSError, UnicodeError) as exc:
            raise RecoveryIntegrityError("Workspace identity can no longer be verified; execution is stopped") from exc

    def reconcile_owners(self) -> None:
        owners = self.store.rows("SELECT o.* FROM owners o JOIN workspaces w USING(workspace_id) WHERE w.project_id=? AND o.state='active' AND o.owner_token<>?", (self.project_id, self.owner_token))
        for owner in owners:
            same_workspace = owner["workspace_id"] == self.workspace_id
            probe = None
            if not same_workspace:
                probe = _FileLock(self.store.lock_dir / (owner["workspace_id"] + ".lock"))
                try:
                    probe.acquire()
                except WorkspaceBusy:
                    continue
            try:
                with self.store.transaction() as conn:
                    # The lock proves the old owner has gone; not that its external
                    # subprocess, remote request, or detached descendant stopped.
                    conn.execute("UPDATE owners SET state='lost' WHERE owner_token=?", (owner["owner_token"],))
                    conn.execute("UPDATE operations SET state='outcome_unknown',observation=? WHERE owner_token=? AND state='intent'", ("Execution owner disappeared; external outcome is unconfirmed", owner["owner_token"]))
                    conn.execute("UPDATE operations SET state='interrupted',observation=? WHERE owner_token=? AND state IN ('planned','waiting_approval')", ("Interrupted before dispatch intent", owner["owner_token"]))
                    conn.execute("UPDATE runs SET state='interrupted',ended_at=? WHERE owner_token=? AND state='running'", (now(), owner["owner_token"]))
            finally:
                if probe:
                    probe.release()

    def pending(self) -> list[dict]:
        self.reconcile_owners()
        rows = self.store.rows("SELECT o.* FROM operations o WHERE project_id=? AND state='outcome_unknown' AND NOT EXISTS(SELECT 1 FROM resolutions r WHERE r.operation_id=o.operation_id) ORDER BY created_at,operation_id", (self.project_id,))
        for row in rows:
            row["arguments"] = json.loads(row["arguments"])
            if row["result"] is not None:
                row["result"] = json.loads(row["result"])
        return rows

    def pending_restores(self, *, allow_restore_id=None) -> list[dict]:
        tables = self.store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='file_restores'")
        if not tables:
            return []
        return self.store.rows("SELECT restore_id,workspace_id,session_id,state FROM file_restores WHERE workspace_id=? AND state<>'complete' AND restore_id<>?", (self.workspace_id, allow_restore_id or ""))

    def ensure_ready(self, *, allow_restore_id=None) -> None:
        pending = self.pending()
        if pending:
            raise RecoveryRequired(pending)
        restores = self.pending_restores(allow_restore_id=allow_restore_id)
        if restores:
            raise RecoveryRequired(restores, "An interrupted file restore requires /rewind review before continuing")
        with self.store.transaction() as conn:
            self._require_owner(conn, dispatch=True)

    def live_operations(self) -> list[dict]:
        return self.store.rows("SELECT * FROM operations WHERE workspace_id=? AND state='intent'", (self.workspace_id,))

    def ensure_workspace_idle(self, path: str | Path | None = None, *, allow_restore_id=None, ignore_operation_id=None) -> None:
        if path is not None and self.store.register_workspace(path).workspace_id != self.workspace_id:
            identity = self.store.register_workspace(path)
            for owned in [self._tree_root, *self._tree_root._children]:
                if owned.workspace_id == identity.workspace_id and not owned._closed:
                    owned.ensure_workspace_idle(allow_restore_id=allow_restore_id, ignore_operation_id=ignore_operation_id)
                    return
            check = type(self).acquire(path, store=self.store)
            try:
                check.ensure_workspace_idle(allow_restore_id=allow_restore_id, ignore_operation_id=ignore_operation_id)
            finally:
                check.close()
            return
        self.ensure_ready(allow_restore_id=allow_restore_id)
        if any(row["operation_id"] != ignore_operation_id for row in self.live_operations()):
            raise WorkspaceBusy("Workspace still has unfinished execution")

    def begin_run(self, session_id: str | None = None, *, source: str = "user", parent_operation_id=None) -> str:
        self.ensure_ready()
        run_id = identifier("run")
        with self.store.transaction() as conn:
            self._require_owner(conn, dispatch=True)
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, self.workspace_id, self.owner_token, session_id or self.session_id, self.generation, source, parent_operation_id or self.current_operation_id, "running", now(), None))
        self.run_id = run_id
        return run_id

    def end_run(self, run_id: str | None = None, *, state: str = "completed") -> None:
        run_id = run_id or self.run_id
        if run_id is None:
            return
        with self.store.transaction() as conn:
            conn.execute("UPDATE runs SET state=?,ended_at=? WHERE run_id=? AND owner_token=?", (state, now(), run_id, self.owner_token))
        if run_id == self.run_id:
            self.run_id = None
            self.model_turn_id = None

    def begin_operation(self, kind: str, name: str, arguments: Any = None, *, cwd: str | None = None, tool_call_id: str | None = None, parent_operation_id: str | None = None, operation_id: str | None = None, state: str = "intent", allow_restore_id=None) -> str:
        self.ensure_ready(allow_restore_id=allow_restore_id)
        operation_id = operation_id or identifier("operation")
        with self.store.transaction() as conn:
            self._require_owner(conn, dispatch=True)
            pending = conn.execute("SELECT operation_id FROM operations o WHERE project_id=? AND state='outcome_unknown' AND NOT EXISTS(SELECT 1 FROM resolutions r WHERE r.operation_id=o.operation_id)", (self.project_id,)).fetchall()
            if pending:
                raise RecoveryRequired([dict(row) for row in pending])
            conn.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (operation_id, self.project_id, self.workspace_id, self.owner_token, self.run_id, self.session_id, self.generation, self.model_turn_id, parent_operation_id or self.current_operation_id, kind, name, canonical_json(arguments if arguments is not None else {}), str(Path(cwd or self.workspace.root).resolve()), tool_call_id, state, now(), None, None, 0, None))
        if state == "intent":
            self.store.fault("operation_intent_committed")
        return operation_id

    def transition_operation(self, operation_id: str, state: str) -> None:
        allowed = {"waiting_approval": {"planned"}, "intent": {"planned", "waiting_approval"}}
        if state not in allowed:
            raise ValueError("Unsupported operation transition")
        self.ensure_ready()
        with self.store.transaction() as conn:
            self._require_owner(conn, dispatch=True)
            row = conn.execute("SELECT state,owner_token FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None or row["owner_token"] != self.owner_token or row["state"] not in allowed[state]:
                raise RecoveryIntegrityError("Invalid operation lifecycle transition")
            pending = conn.execute("SELECT 1 FROM operations o WHERE project_id=? AND state='outcome_unknown' AND NOT EXISTS(SELECT 1 FROM resolutions r WHERE r.operation_id=o.operation_id) LIMIT 1", (self.project_id,)).fetchone()
            if pending:
                raise RecoveryRequired()
            if state == "intent":
                prepared = conn.execute("SELECT payload FROM recovery_metadata WHERE kind='dispatch' AND item_id=?", (operation_id,)).fetchone()
                if prepared:
                    dispatch = json.loads(prepared[0])
                    conn.execute("UPDATE operations SET arguments=?,cwd=? WHERE operation_id=?", (canonical_json(dispatch["arguments"]), str(Path(dispatch["cwd"]).resolve()), operation_id))
            conn.execute("UPDATE operations SET state=? WHERE operation_id=?", (state, operation_id))
        if state == "intent":
            self.store.fault("operation_intent_committed")

    def start_operation(self, operation_id: str) -> None:
        self.transition_operation(operation_id, "intent")

    def wait_for_approval(self, operation_id: str) -> None:
        self.transition_operation(operation_id, "waiting_approval")

    def finish_not_started(self, operation_id: str, result: Any, *, is_error: bool = True) -> None:
        with self.store.transaction() as conn:
            self._require_owner(conn)
            row = conn.execute("SELECT state,owner_token FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None or row["owner_token"] != self.owner_token or row["state"] not in {"planned", "waiting_approval", "interrupted"}:
                raise RecoveryIntegrityError("An operation with dispatch intent cannot be declared not started")
            conn.execute("UPDATE operations SET state='interrupted',result=?,is_error=?,observed_at=?,observation='Not dispatched' WHERE operation_id=?", (canonical_json(result), int(is_error), now(), operation_id))

    def finish_operation(self, operation_id: str, result: Any, *, is_error: bool = False, observation: str | None = None) -> None:
        if hasattr(result, "content"):
            is_error = bool(getattr(result, "is_error", is_error))
            result = result.content
        with self.store.transaction() as conn:
            self._require_owner(conn)
            row = conn.execute("SELECT * FROM operations WHERE operation_id=? AND owner_token=?", (operation_id, self.owner_token)).fetchone()
            if row is None:
                raise RecoveryIntegrityError("Operation does not belong to this execution owner")
            encoded = canonical_json(result)
            if row["state"] == "completed":
                if row["result"] != encoded or row["is_error"] != int(is_error):
                    raise RecoveryIntegrityError("Operation completion conflicts with its original result")
                return
            conn.execute("UPDATE operations SET state='completed',result=?,is_error=?,observed_at=?,observation=? WHERE operation_id=?", (encoded, int(is_error), now(), observation, operation_id))
        self.store.fault("operation_result_committed")

    def mark_unknown(self, operation_id: str, reason: str) -> None:
        with self.store.transaction() as conn:
            self._require_owner(conn)
            conn.execute("UPDATE operations SET state='outcome_unknown',observation=? WHERE operation_id=? AND state<>'completed'", (reason, operation_id))

    def not_started(self, operation_id: str, reason: str) -> None:
        with self.store.transaction() as conn:
            self._require_owner(conn)
            conn.execute("UPDATE operations SET state='interrupted',observation=?,observed_at=? WHERE operation_id=? AND state IN ('planned','waiting_approval','intent')", (reason, now(), operation_id))

    def acknowledge(self, operation_ids: list[str] | str, note: str, *, decision: str = "accept_unknown") -> None:
        if not note.strip():
            raise ValueError("A recovery decision needs a user note")
        ids = [operation_ids] if isinstance(operation_ids, str) else operation_ids
        with self.store.transaction() as conn:
            self._require_owner(conn)
            for operation_id in ids:
                row = conn.execute("SELECT state,project_id FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
                if row is None or row["project_id"] != self.project_id or row["state"] != "outcome_unknown":
                    raise RecoveryRequired(message="Only unknown outcomes can be acknowledged; live execution must finish first")
                conn.execute("INSERT INTO resolutions VALUES(?,?,?,?,?)", (identifier("resolution"), operation_id, decision, note, now()))

    @contextmanager
    def session_binding(self, session):
        """Borrow a projection binding without replacing the active UI session."""
        if self.session is session:
            yield self
            return
        path = session._sessions_dir / f"{session.session_id}.jsonl"
        key, generation = self.store.register_session(session.session_id, path, self.workspace_id)
        probe = None
        if key != self.session_key:
            probe = _FileLock(self.store.lock_dir / ("session-" + hashlib.sha256(str(path.absolute()).encode()).hexdigest() + ".lock"))
            probe.acquire()
        previous = self.session, self.session_id, self.session_key, self.generation
        try:
            self.session, self.session_id, self.session_key, self.generation = session, session.session_id, key, generation
            session._recovery = self
            session._generation = generation
            yield self
        finally:
            generation = self.generation if key == previous[2] else previous[3]
            self.session, self.session_id, self.session_key = previous[:3]
            self.generation = generation
            if probe:
                probe.release()

    def bind_session(self, session, *, shared_with=None) -> None:
        path = session._sessions_dir / f"{session.session_id}.jsonl"
        session_key, generation = self.store.register_session(session.session_id, path, self.workspace_id)
        host = self._tree_root
        previous_key = host._session_lock_key
        previous = getattr(session, "_recovery", None)
        sharing = (shared_with is not None or
                   (previous is not None and previous is not self and previous._tree_root is host))
        if previous_key != session_key:
            lock = _FileLock(self.store.lock_dir / ("session-" + hashlib.sha256(str(path.absolute()).encode()).hexdigest() + ".lock"))
            lock.acquire()
            if host._session_lock:
                host._session_lock.release()
            host._session_lock = lock
            host._session_lock_key = session_key
        # One main conversation projection lease belongs to the runtime tree.
        # Child actor conversations are ledger-only and never take this lease.
        for runtime in [host, *host._children]:
            runtime._session_host = host
            runtime.session = session
            runtime.session_id = session.session_id
            runtime.session_key = session_key
            runtime.generation = generation
        session._recovery = self
        session._generation = generation
        if not sharing:
            self.reconcile_session()

    def persist_message(self, message, session=None) -> None:
        if session is not None and session is not self.session:
            self.bind_session(session)
        if self.session is None:
            return
        if message.role == "assistant" and self.session_key not in getattr(message, "_recovery_record_ids", {}):
            self.model_turn_id = identifier("modelturn")
        self.session.append(message)

    def persist_child_message(self, message, agent_id: str) -> None:
        """Retain a child's complete envelope without changing the main chat."""
        from nanocursor.memory.session import SessionRecord
        if message.role == "assistant":
            self.model_turn_id = identifier("modelturn")
        record_id = identifier("childmessage")
        self.store.put_metadata("child_message", record_id, {
            "record_id": record_id, "agent_id": agent_id,
            "run_id": self.run_id, "model_turn_id": self.model_turn_id,
            "workspace_id": self.workspace_id, "session_id": self.session_id,
            "generation": self.generation,
            "records": [json.loads(record.to_jsonl()) for record in SessionRecord.from_message(message)],
        })

    def result_record(self, tool_call_id: str, *, include_unknown: bool = False):
        rows = self.store.rows("SELECT * FROM operations WHERE session_id=? AND generation=? AND tool_call_id=? AND ((result IS NOT NULL AND state IN ('completed','interrupted')) OR (? AND state='outcome_unknown')) AND (? IS NOT NULL OR workspace_id=?) AND (? IS NULL OR model_turn_id=?) ORDER BY created_at DESC", (self.session_id, self.generation, tool_call_id, int(include_unknown), self.model_turn_id, self.workspace_id, self.model_turn_id, self.model_turn_id))
        return self._operation_record(rows[0]) if rows else None

    def tool_record(self, tool_call_id: str):
        """Projection source including explicit unknown host observations."""
        return self.result_record(tool_call_id, include_unknown=True)

    def _operation_record(self, operation: dict) -> dict:
        completed = operation["state"] == "completed"
        interrupted = operation["state"] == "interrupted"
        placeholder = ("[Recovery] Interrupted before dispatch. This operation was not started and will not be replayed."
                       if interrupted else "[Recovery] This operation was interrupted. Its external outcome is unknown; this is a host recovery marker, not a tool result. Do not replay it automatically.")
        return {"type": "tool_result", "content": json.loads(operation["result"]) if operation["result"] is not None else placeholder, "timestamp": operation["observed_at"] or operation["created_at"], "tool_use_id": operation["tool_call_id"], "is_error": bool(operation["is_error"]) if completed else True, "record_id": "result_" + operation["operation_id"], "generation": operation["generation"], "run_id": operation["run_id"], "model_turn_id": operation["model_turn_id"], "recovery_source": {"operation_id": operation["operation_id"], "state": operation["state"], "observed_at": operation["observed_at"]}}

    def reconcile_session(self) -> None:
        if self.session is None:
            return
        records = self.store.projection_records(self.session_key)
        # Reconstruct results only for the generation still being replayed.
        calls = set()
        results = set()
        last_boundary = max((i for i, record in enumerate(records)
                             if record["type"] in {"compact_boundary", "history_boundary"}), default=-1)
        for record in records[last_boundary + 1:]:
            if record.get("generation", 0) != self.generation:
                continue
            if record["type"] == "assistant" and isinstance(record["content"], list):
                calls.update((record.get("model_turn_id"), block["id"]) for block in record["content"] if isinstance(block, dict) and block.get("type") == "tool_use")
            elif record["type"] == "tool_result":
                results.add((record.get("model_turn_id"), record.get("tool_use_id")))
        for turn_id, call_id in calls - results:
            operations = self.store.rows("SELECT * FROM operations WHERE session_id=? AND generation=? AND tool_call_id=? AND model_turn_id IS ? ORDER BY created_at DESC", (self.session_id, self.generation, call_id, turn_id))
            if operations and operations[0]["state"] in ("completed", "outcome_unknown", "interrupted"):
                self.store.enqueue_record(self.session_key, self._operation_record(operations[0]))
            elif not operations:
                # Complete envelope persisted, but no intent was committed.
                self.store.enqueue_record(self.session_key, {"type": "tool_result", "content": "[Recovery] Interrupted before execution intent; this call was not dispatched and will not be replayed.", "timestamp": now(), "tool_use_id": call_id, "is_error": True, "record_id": f"notstarted_{self.session_key}_{self.generation}_{turn_id}_{call_id}", "generation": self.generation, "model_turn_id": turn_id, "recovery_source": {"state": "not_started"}})
        for operation in self.store.rows("SELECT result FROM operations WHERE session_id=? AND generation=? AND state='completed' AND kind IN ('background','skill','team_actor')", (self.session_id, self.generation)):
            result = json.loads(operation["result"]) if operation["result"] else None
            notice = result.get("notification") if isinstance(result, dict) else None
            if isinstance(notice, dict) and notice.get("session_key") == self.session_key:
                payload = notice.get("record")
                if isinstance(payload, dict) and payload.get("generation") == self.generation:
                    self.store.enqueue_record(self.session_key, payload)
        for kind in ("background_result", "skill_result"):
            for row in self.store.rows("SELECT item_id,payload FROM recovery_metadata WHERE kind=?", (kind,)):
                operations = self.store.rows("SELECT operation_id FROM operations WHERE operation_id=? AND session_id=? AND generation=?", (row["item_id"], self.session_id, self.generation))
                if not operations:
                    continue
                result = json.loads(row["payload"])
                notice = result.get("notification") if isinstance(result, dict) else None
                if isinstance(notice, dict) and notice.get("session_key") == self.session_key:
                    payload = notice.get("record")
                    if isinstance(payload, dict) and payload.get("generation") == self.generation:
                        self.store.enqueue_record(self.session_key, payload)
        path = self.session._sessions_dir / f"{self.session_id}.jsonl"
        self.session._file.flush()
        self.store.project(self.session_key, path)
        # Atomic projection replacement invalidates old open file descriptors.
        self.session._file.close()
        self.session._file = path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._closed:
            return
        failures = []
        if self._tree_root is self:
            for child in self._children:
                try:
                    child.close()
                except BaseException as exc:
                    # One failed close must not leave other checkout/session
                    # leases locked and mask the original persistence failure.
                    failures.append(exc)
        try:
            if not self.store.failed and not self.store.closed:
                with self.store.transaction() as conn:
                    conn.execute("UPDATE operations SET state='outcome_unknown',observation=? WHERE owner_token=? AND state='intent'", ("Owner closed without an observed terminal result", self.owner_token))
                    conn.execute("UPDATE operations SET state='interrupted',observation=? WHERE owner_token=? AND state IN ('planned','waiting_approval')", ("Owner closed before dispatch intent", self.owner_token))
                    conn.execute("UPDATE runs SET state='interrupted',ended_at=? WHERE owner_token=? AND state='running'", (now(), self.owner_token))
                    conn.execute("UPDATE owners SET state='closed' WHERE owner_token=?", (self.owner_token,))
        except BaseException as exc:
            failures.append(exc)
        finally:
            self._closed = True
            if _current.get() is self:
                _current.set(None)
            if self._session_lock:
                self._session_lock.release()
            self._lock.release()
        if failures:
            raise failures[0]

    release = close
