"""Local durable execution facts and immutable content, shared by all entrypoints.

Only metadata is transacted. No transaction spans a model call or an external
operation. SQLite and JSONL are joined by an idempotent full-payload outbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import RecoveryIntegrityError, RecoveryStorageError, WorkspaceIdentity


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS recovery_schema(version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS workspaces(workspace_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root TEXT UNIQUE NOT NULL, git_common_dir TEXT, fingerprint TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS owners(owner_token TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id), pid INTEGER NOT NULL, started_at TEXT NOT NULL, state TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sessions(session_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, path TEXT UNIQUE NOT NULL, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id), generation INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id), owner_token TEXT NOT NULL, session_id TEXT, generation INTEGER NOT NULL, source TEXT NOT NULL, parent_operation_id TEXT, state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT)",
    "CREATE TABLE IF NOT EXISTS operations(operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id), owner_token TEXT NOT NULL, run_id TEXT, session_id TEXT, generation INTEGER NOT NULL, model_turn_id TEXT, parent_operation_id TEXT, kind TEXT NOT NULL, name TEXT NOT NULL, arguments TEXT NOT NULL, cwd TEXT NOT NULL, tool_call_id TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL, observed_at TEXT, result TEXT, is_error INTEGER NOT NULL DEFAULT 0, observation TEXT)",
    "CREATE INDEX IF NOT EXISTS operations_gate ON operations(project_id,state)",
    "CREATE TABLE IF NOT EXISTS resolutions(resolution_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id), decision TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS outbox(sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT UNIQUE NOT NULL, session_key TEXT NOT NULL REFERENCES sessions(session_key), generation INTEGER NOT NULL, payload TEXT NOT NULL, projected INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS outbox_session ON outbox(session_key,sequence)",
    "CREATE TABLE IF NOT EXISTS recovery_metadata(kind TEXT NOT NULL, item_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,item_id))",
)


class RecoveryStore:
    def __init__(self, root: Path | str | None = None, *, fault_hook: Callable[[str], None] | None = None):
        from nanocursor.runtime import app_home
        self.root = Path(root) if root is not None else app_home() / "recovery"
        self.root = self.root.expanduser().absolute()
        self._mutex = threading.RLock()
        self.fault_hook = fault_hook
        self.failed = False
        self.closed = False
        try:
            from nanocursor.storage import private_directory
            missing = []
            for candidate in (self.root, *self.root.parents):
                if candidate.exists():
                    break
                missing.append(candidate)
            private_directory(self.root)
            for created in reversed(missing):
                sync_directory(created.parent)
            self.blob_dir = self.root / "objects"
            self.lock_dir = self.root / "locks"
            private_directory(self.blob_dir)
            private_directory(self.lock_dir)
            db_path = self.root / "recovery.sqlite3"
            if db_path.is_symlink():
                raise RecoveryIntegrityError("Recovery database must not be a symbolic link")
            self.connection = sqlite3.connect(db_path, timeout=5, isolation_level=None, check_same_thread=False)
            os.chmod(db_path, 0o600)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=EXTRA")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA fullfsync=ON")
            with self.transaction() as conn:
                for statement in _SCHEMA:
                    conn.execute(statement)
                versions = [row[0] for row in conn.execute("SELECT version FROM recovery_schema")]
                if not versions:
                    conn.execute("INSERT INTO recovery_schema VALUES(1)")
                elif versions != [1]:
                    raise RecoveryIntegrityError("Unsupported recovery database schema")
            sync_directory(self.root)
        except (OSError, sqlite3.Error) as exc:
            raise RecoveryStorageError("Cannot initialize durable recovery storage") from exc

    def fault(self, phase: str) -> None:
        if self.fault_hook is not None:
            try:
                self.fault_hook(phase)
            except OSError as exc:
                self.failed = True
                raise RecoveryStorageError("Injected persistence failure; execution is stopped") from exc

    @contextmanager
    def transaction(self):
        with self._mutex:
            if self.closed:
                raise RecoveryStorageError("Recovery storage is closed")
            if self.failed:
                raise RecoveryStorageError("Recovery storage previously failed; restart and inspect before executing")
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
                self.fault("before_commit")
                self.connection.commit()
            except BaseException as exc:
                try:
                    self.connection.rollback()
                except sqlite3.Error:
                    self.failed = True
                if isinstance(exc, RecoveryStorageError):
                    self.failed = True
                    raise
                if isinstance(exc, (OSError, sqlite3.Error)):
                    self.failed = True
                    raise RecoveryStorageError("Recovery transaction failed; execution is stopped") from exc
                raise

    def rows(self, sql: str, parameters=()) -> list[dict]:
        with self._mutex:
            try:
                return [dict(row) for row in self.connection.execute(sql, parameters)]
            except sqlite3.Error as exc:
                raise RecoveryStorageError("Cannot read recovery evidence") from exc

    def close(self) -> None:
        with self._mutex:
            if not self.closed:
                self.connection.close()
                self.closed = True

    def put_blob(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        target = self.blob_dir / digest
        if target.exists():
            if self.get_blob(digest) != content:
                raise RecoveryIntegrityError("Content object mismatch")
            return digest
        temporary = self.blob_dir / (".tmp-" + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self.fault("blob_before_publish")
            os.replace(temporary, target)
            sync_directory(self.blob_dir)
            return digest
        except OSError as exc:
            self.failed = True
            raise RecoveryStorageError("Cannot durably publish recovery content") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def get_blob(self, digest: str) -> bytes:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise RecoveryIntegrityError("Invalid content object identity")
        target = self.blob_dir / digest
        try:
            if target.is_symlink():
                raise RecoveryIntegrityError("Content object is a symbolic link")
            content = target.read_bytes()
        except OSError as exc:
            raise RecoveryStorageError("Recovery content is unavailable") from exc
        if hashlib.sha256(content).hexdigest() != digest:
            raise RecoveryIntegrityError("Recovery content checksum mismatch")
        return content

    def register_workspace(self, path: Path | str) -> WorkspaceIdentity:
        root = Path(path).expanduser().resolve(strict=True)
        common = None
        try:
            result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel", "--git-common-dir"], capture_output=True, text=True, check=True, timeout=5)
            lines = result.stdout.splitlines()
            root = Path(lines[0]).resolve(strict=True)
            common_path = Path(lines[1])
            common = str((root / common_path).resolve(strict=True)) if not common_path.is_absolute() else str(common_path.resolve(strict=True))
        except (subprocess.SubprocessError, OSError, IndexError):
            pass
        stat = root.stat()
        fingerprint = f"{stat.st_dev}:{stat.st_ino}"
        if common:
            git_stat = Path(common).stat()
            project_source = f"git:{common}:{git_stat.st_dev}:{git_stat.st_ino}"
        else:
            project_source = f"directory:{root}:{fingerprint}"
        project_id = "project_" + hashlib.sha256(project_source.encode()).hexdigest()[:32]
        with self.transaction() as conn:
            existing = conn.execute("SELECT * FROM workspaces WHERE root=?", (str(root),)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint or existing["git_common_dir"] != common:
                    raise RecoveryIntegrityError("Workspace identity changed; explicit reassociation is required")
                return WorkspaceIdentity(existing["project_id"], existing["workspace_id"], str(root), common)
            moved = conn.execute("SELECT workspace_id,root FROM workspaces WHERE fingerprint=? AND root<>? AND NOT EXISTS(SELECT 1 FROM recovery_metadata m WHERE m.kind='removed_workspace' AND m.item_id=workspaces.workspace_id)", (fingerprint, str(root))).fetchone()
            if moved:
                raise RecoveryIntegrityError("A registered workspace moved; explicit reassociation is required")
            if not common:
                # A registered parent remains the identity when launched beneath it.
                for row in conn.execute("SELECT * FROM workspaces WHERE git_common_dir IS NULL"):
                    other = Path(row["root"])
                    if root.is_relative_to(other):
                        if f"{other.stat().st_dev}:{other.stat().st_ino}" != row["fingerprint"]:
                            raise RecoveryIntegrityError("Registered workspace was replaced")
                        return WorkspaceIdentity(row["project_id"], row["workspace_id"], row["root"], None)
                    if other.is_relative_to(root):
                        raise RecoveryIntegrityError("This directory contains a registered workspace; use its registered root or explicitly reassociate it")
            workspace_id = identifier("workspace")
            conn.execute("INSERT INTO workspaces VALUES(?,?,?,?,?)", (workspace_id, project_id, str(root), common, fingerprint))
        return WorkspaceIdentity(project_id, workspace_id, str(root), common)

    def mark_workspace_removed(self, workspace_id: str) -> None:
        """Retain historical facts while releasing a deliberately removed path."""
        with self.transaction() as conn:
            row = conn.execute("SELECT root FROM workspaces WHERE workspace_id=?", (workspace_id,)).fetchone()
            if row is None:
                raise RecoveryIntegrityError("Cannot remove an unknown workspace identity")
            conn.execute("INSERT OR REPLACE INTO recovery_metadata VALUES(?,?,?)", ("removed_workspace", workspace_id, canonical_json({"original_root": row[0], "removed_at": now()})))
            conn.execute("UPDATE workspaces SET root=? WHERE workspace_id=?", (row[0] + "/.removed-" + workspace_id, workspace_id))

    def put_metadata(self, kind: str, item_id: str, payload: Any) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT INTO recovery_metadata VALUES(?,?,?) ON CONFLICT(kind,item_id) DO UPDATE SET payload=excluded.payload", (kind, item_id, canonical_json(payload)))

    def get_metadata(self, kind: str, item_id: str, default=None):
        rows = self.rows("SELECT payload FROM recovery_metadata WHERE kind=? AND item_id=?", (kind, item_id))
        return json.loads(rows[0]["payload"]) if rows else default

    def list_metadata(self, kind: str) -> list:
        return [json.loads(row["payload"]) for row in self.rows("SELECT payload FROM recovery_metadata WHERE kind=? ORDER BY item_id", (kind,))]

    def register_session(self, session_id: str, path: Path, workspace_id: str) -> tuple[str, int]:
        path = path.absolute()
        key = "session_" + hashlib.sha256(str(path).encode()).hexdigest()
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,0)", (key, session_id, str(path), workspace_id))
            row = conn.execute("SELECT generation FROM sessions WHERE session_key=?", (key,)).fetchone()
        return key, row[0]

    def enqueue_record(self, session_key: str, payload: dict, *, new_generation: bool = False) -> dict:
        payload = dict(payload)
        for optional in ("run_id", "model_turn_id", "recovery_source", "thinking_blocks", "tool_use_id", "memory_context"):
            if payload.get(optional) is None:
                payload.pop(optional, None)
        record_id = payload.setdefault("record_id", identifier("record"))
        with self.transaction() as conn:
            existing = conn.execute("SELECT payload,session_key FROM outbox WHERE record_id=?", (record_id,)).fetchone()
            if existing:
                if existing[1] != session_key:
                    raise RecoveryIntegrityError("A record identity cannot belong to two sessions")
                previous = json.loads(existing[0])
                candidate = dict(payload)
                candidate.setdefault("generation", previous["generation"])
                if canonical_json(candidate) != existing[0]:
                    raise RecoveryIntegrityError("A session record identity has conflicting content")
                return previous
            row = conn.execute("SELECT generation FROM sessions WHERE session_key=?", (session_key,)).fetchone()
            if row is None:
                raise RecoveryIntegrityError("Unknown session projection")
            generation = row[0] + int(new_generation)
            payload["generation"] = generation
            conn.execute("INSERT INTO outbox(record_id,session_key,generation,payload) VALUES(?,?,?,?)", (record_id, session_key, generation, canonical_json(payload)))
            if new_generation:
                conn.execute("UPDATE sessions SET generation=? WHERE session_key=?", (generation, session_key))
        self.fault("outbox_committed")
        return payload

    def projection_records(self, session_key: str) -> list[dict]:
        return [json.loads(row["payload"]) for row in self.rows("SELECT payload FROM outbox WHERE session_key=? ORDER BY sequence", (session_key,))]

    def project(self, session_key: str, path: Path) -> None:
        """Verify every known record, preserve corrupt tails, and fsync projection.

        A complete unknown-schema line or any middle corruption is an integrity
        failure. Only the final malformed/truncated line can be quarantined.
        """
        from nanocursor.memory.session import SessionRecord
        expected = self.projection_records(session_key)
        try:
            raw = path.read_bytes() if path.exists() else b""
            lines = raw.splitlines(keepends=True)
            known: dict[str, dict] = {}
            good: list[bytes] = []
            for index, line in enumerate(lines):
                if not line.strip():
                    good.append(line)
                    continue
                try:
                    data = json.loads(line)
                except (ValueError, UnicodeError):
                    if index != len(lines) - 1:
                        raise RecoveryIntegrityError("Session contains middle corruption; read-only diagnosis required") from None
                    evidence = path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex)
                    self._write_private(evidence, raw)
                    self._write_private(path, b"".join(good))
                    raw = b"".join(good)
                    break
                if not isinstance(data, dict) or SessionRecord.from_jsonl(json.dumps(data)) is None:
                    raise RecoveryIntegrityError("Session contains unsupported or invalid record schema")
                record_id = data.get("record_id")
                if record_id:
                    if record_id in known:
                        raise RecoveryIntegrityError("Session contains duplicate record identities")
                    known[record_id] = data
                good.append(line)
            missing = []
            for payload in expected:
                actual = known.get(payload["record_id"])
                if actual is not None and canonical_json(actual) != canonical_json(payload):
                    raise RecoveryIntegrityError("JSONL and execution ledger disagree for the same record")
                if actual is None:
                    missing.append(payload)
            if missing or (raw and not raw.endswith(b"\n")):
                expected_ids = {p["record_id"] for p in expected}
                present_ids = [rid for rid in known if rid in expected_ids]
                missing_ids = [p["record_id"] for p in missing]
                expected_order = [p["record_id"] for p in expected]
                append_only = (present_ids + missing_ids == expected_order
                               and all(rid in known for rid in present_ids)
                               and (not raw or raw.endswith(b"\n")))
                self.fault("projection_before_write")
                if append_only:
                    existed = path.exists()
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    with os.fdopen(fd, "ab") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        for payload in missing:
                            stream.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
                        stream.flush()
                        os.fsync(stream.fileno())
                    if not existed:
                        sync_directory(path.parent)
                else:
                    # A lost older line must precede a newer rewind boundary.
                    legacy = [line for line in good if not line.strip() or json.loads(line).get("record_id") not in expected_ids]
                    rebuilt = b"".join(line if line.endswith(b"\n") else line + b"\n" for line in legacy)
                    rebuilt += b"".join((json.dumps(p, ensure_ascii=False) + "\n").encode() for p in expected)
                    self._write_private(path, rebuilt)
            else:
                # Confirmation requires durable existing bytes as well.
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            self.fault("projection_synced")
            with self.transaction() as conn:
                conn.execute("UPDATE outbox SET projected=1 WHERE session_key=?", (session_key,))
        except RecoveryStorageError:
            self.failed = True
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            self.failed = True
            raise RecoveryStorageError("Cannot safely project the durable session") from exc

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
