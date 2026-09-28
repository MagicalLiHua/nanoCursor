"""Durable, workspace-scoped file checkpoints and resumable restores.

The execution ledger and these tables share one RecoveryStore. File history does
not infer that an unknown tool completed, and never re-executes tools. Its local
hash comparisons are evidence about file contents only.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nanocursor.conversation import Message, ThinkingBlock, ToolResultBlock, ToolUseBlock
from nanocursor.tools.file_io import MAX_FILE_BYTES, make_parents_durable, path_lock, sync_directory, validate_regular_path

MAX_SNAPSHOTS = 100


class RewindError(RuntimeError):
    pass


@dataclass(frozen=True)
class Backup:
    backup_path: str
    version: int
    timestamp: float
    exists: bool = True
    digest: str = ""
    mode: int | None = None


@dataclass
class Snapshot:
    message_index: int
    user_text: str
    backups: dict[str, Backup] = field(default_factory=dict)
    timestamp: float = 0.0
    conversation: list[Message] | None = None
    env_injected: bool = False
    ltm_injected: bool = False
    checkpoint_id: str = ""
    workspace_id: str = ""
    generation: int = 0
    run_id: str | None = None
    baseline_seq: int = 0
    parent_id: str | None = None
    pinned: bool = False


@dataclass
class RestoreFile:
    path: str
    before: Backup
    target: Backup
    edit_ids: list[str] = field(default_factory=list)
    diff: str = ""

    @property
    def action(self) -> str:
        return "delete" if not self.target.exists else "create" if not self.before.exists else "modify"


@dataclass
class RestorePreview:
    checkpoint_id: str
    workspace_id: str
    files: list[RestoreFile] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)


def _message_from_dict(data: dict[str, Any]) -> Message:
    data = dict(data)
    data["tool_uses"] = [ToolUseBlock(**v) for v in data.get("tool_uses", [])]
    data["tool_results"] = [ToolResultBlock(**v) for v in data.get("tool_results", [])]
    data["thinking_blocks"] = [ThinkingBlock(**v) for v in data.get("thinking_blocks", [])]
    return Message(**data)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _same(left: Backup, right: Backup) -> bool:
    return (left.exists, left.digest, left.mode) == (right.exists, right.digest, right.mode)


class FileHistory:
    """Persistent checkpoints; production callers inject the runtime's store.

    The local-store default preserves the standalone/legacy embedding API.
    The CLI supplies its shared app-home store so removing a worktree does not
    remove the only backups. Old unindexed backup files are never guessed into
    the new index.
    """

    def __init__(self, base_dir: str, session_id: str, *, store: Any = None,
                 workspace_id: str | None = None, generation: int = 0) -> None:
        from nanocursor.recovery import RecoveryStore
        self.root = Path(base_dir).resolve()
        self.session_id = session_id
        self.generation = generation
        self.store = store or RecoveryStore(root=self.root / ".nanocursor" / "recovery")
        identity = self.store.register_workspace(self.root)
        self.root = Path(identity.root)
        self.workspace_id = workspace_id or identity.workspace_id
        if self.workspace_id != identity.workspace_id:
            raise RewindError("Checkpoint workspace does not match the requested directory")
        self._lock = threading.RLock()
        self._current_checkpoint: str | None = None
        self._legacy_pending: dict[str, Backup] = {}
        self._create_schema()
        heads = self._rows("SELECT checkpoint_id FROM checkpoint_heads WHERE workspace_id=? AND session_id=?",
                           (self.workspace_id, self.session_id))
        if heads:
            self._current_checkpoint = heads[0]["checkpoint_id"]

    def _create_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS checkpoint_heads (
                workspace_id TEXT NOT NULL, session_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
                PRIMARY KEY(workspace_id, session_id))""",
            """CREATE TABLE IF NOT EXISTS checkpoint_records (
                checkpoint_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL, generation INTEGER NOT NULL,
                run_id TEXT, created REAL NOT NULL, baseline_seq INTEGER NOT NULL,
                parent_id TEXT, pinned INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS file_edit_records (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, edit_id TEXT UNIQUE NOT NULL,
                workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                generation INTEGER NOT NULL, checkpoint_id TEXT, operation_id TEXT,
                path TEXT NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                state TEXT NOT NULL, barrier INTEGER NOT NULL DEFAULT 0,
                previous_seq INTEGER NOT NULL DEFAULT 0, undone_by TEXT, created REAL NOT NULL)""",
            """CREATE INDEX IF NOT EXISTS file_edits_workspace_path
                ON file_edit_records(workspace_id, path, seq)""",
            """CREATE TABLE IF NOT EXISTS file_restores (
                restore_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
                state TEXT NOT NULL, option INTEGER NOT NULL, created REAL NOT NULL,
                conversation_record_id TEXT NOT NULL, payload TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS file_restore_items (
                restore_id TEXT NOT NULL, path TEXT NOT NULL, position INTEGER NOT NULL,
                before_state TEXT NOT NULL, target_state TEXT NOT NULL,
                edit_ids TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY (restore_id, path),
                FOREIGN KEY (restore_id) REFERENCES file_restores(restore_id))""",
            """CREATE TABLE IF NOT EXISTS checkpoint_uncovered (
                entry_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                session_id TEXT NOT NULL, checkpoint_id TEXT, description TEXT NOT NULL,
                created REAL NOT NULL)""",
        ]
        with self.store.transaction() as db:
            for statement in statements:
                db.execute(statement)

    def _rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self.store.rows(sql, parameters)

    def _max_seq(self) -> int:
        rows = self._rows("SELECT COALESCE(MAX(seq), 0) AS seq FROM file_edit_records WHERE workspace_id=?",
                          (self.workspace_id,))
        return int(rows[0]["seq"])

    def _blob_path(self, digest: str) -> str:
        # Backup paths are display/legacy compatibility; get_blob is authoritative.
        blob_path = getattr(self.store, "blob_path", None)
        if callable(blob_path):
            return str(blob_path(digest))
        return str(self.store.blob_dir / digest)

    def _capture(self, path: str, *, persist: bool = True) -> Backup:
        fp = validate_regular_path(path)
        try:
            with fp.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                if stat.st_size > MAX_FILE_BYTES:
                    raise RewindError(f"File exceeds the {MAX_FILE_BYTES} byte checkpoint limit: {path}")
                data = stream.read(MAX_FILE_BYTES + 1)
                final = os.fstat(stream.fileno())
            if len(data) > MAX_FILE_BYTES or (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (
                    final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns):
                raise RewindError(f"File changed while capturing checkpoint: {path}")
        except FileNotFoundError:
            return Backup("", 0, time.time(), exists=False)
        digest = self.store.put_blob(data) if persist else hashlib.sha256(data).hexdigest()
        return Backup(self._blob_path(digest), 0, time.time(), digest=digest, mode=stat.st_mode & 0o777)

    def _content_backup(self, content: str, before: Backup) -> Backup:
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise RewindError(f"Content exceeds the {MAX_FILE_BYTES} byte checkpoint limit")
        digest = self.store.put_blob(data)
        return Backup(self._blob_path(digest), 0, time.time(), digest=digest,
                      mode=before.mode if before.exists else 0o600)

    def _protected(self, path: Path) -> bool:
        if not path.is_relative_to(self.root):
            return False
        if ".git" in path.relative_to(self.root).parts:
            raise RewindError("Git metadata cannot be edited by a protected file tool")
        store_root = Path(self.store.root).resolve()
        if path == store_root or path.is_relative_to(store_root):
            raise RewindError("Recovery storage cannot be edited by a protected file tool")
        return True

    def _validate_workspace(self) -> None:
        identity = self.store.register_workspace(self.root)
        if identity.workspace_id != self.workspace_id or Path(identity.root) != self.root:
            raise RewindError("Workspace identity changed; old checkpoints are available for inspection only")

    def note_uncovered(self, description: str, *, entry_id: str | None = None) -> None:
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO checkpoint_uncovered VALUES(?,?,?,?,?,?)",
                       (entry_id or uuid.uuid4().hex, self.workspace_id, self.session_id,
                        self._current_checkpoint, description, time.time()))

    def begin_checkpoint(self, msg_index: int, user_text: str, *, conversation: list[Message] | None = None,
                         env_injected: bool = False, ltm_injected: bool = False,
                         run_id: str | None = None, generation: int | None = None) -> Snapshot:
        """Capture the task's origin before its first edit (lazy file backups)."""
        with self._lock:
            if generation is not None:
                self.generation = generation
            if run_id:
                existing = self._rows("SELECT payload FROM checkpoint_records WHERE workspace_id=? AND run_id=?",
                                      (self.workspace_id, run_id))
                if existing:
                    snapshot = self._snapshot_from_payload(existing[0]["payload"])
                    self._current_checkpoint = snapshot.checkpoint_id
                    return snapshot
            snapshot = Snapshot(msg_index, user_text, timestamp=time.time(),
                                conversation=copy.deepcopy(conversation), env_injected=env_injected,
                                ltm_injected=ltm_injected, checkpoint_id=uuid.uuid4().hex,
                                workspace_id=self.workspace_id, generation=self.generation,
                                run_id=run_id, baseline_seq=self._max_seq(), parent_id=self._current_checkpoint)
            # Snapshot states are also useful for preview/export; new paths use
            # their first durable edit's before state when planning a restore.
            latest = self._rows("SELECT * FROM file_edit_records WHERE workspace_id=? AND undone_by IS NULL ORDER BY seq",
                                (self.workspace_id,))
            for row in latest:
                if row["session_id"] == self.session_id:
                    snapshot.backups[row["path"]] = Backup(**json.loads(row["after_state"]))
            with self.store.transaction() as db:
                db.execute("INSERT INTO checkpoint_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (snapshot.checkpoint_id, self.workspace_id, self.session_id, self.generation,
                            run_id, snapshot.timestamp, snapshot.baseline_seq, snapshot.parent_id, 0,
                            _dump(asdict(snapshot))))
                db.execute("INSERT OR REPLACE INTO checkpoint_heads VALUES(?,?,?)",
                           (self.workspace_id, self.session_id, snapshot.checkpoint_id))
            self._current_checkpoint = snapshot.checkpoint_id
            self.prune_unreferenced()
            return snapshot

    def make_snapshot(self, msg_index: int, user_text: str, **kwargs: Any) -> Snapshot:
        """Compatibility: callers using this method create a point *now*."""
        return self.begin_checkpoint(msg_index, user_text, **kwargs)

    @staticmethod
    def _snapshot_from_payload(payload: str) -> Snapshot:
        data = json.loads(payload)
        data["backups"] = {path: Backup(**b) for path, b in data["backups"].items()}
        if data["conversation"] is not None:
            data["conversation"] = [_message_from_dict(m) for m in data["conversation"]]
        return Snapshot(**data)

    def get_snapshots(self) -> list[Snapshot]:
        rows = self._rows("SELECT payload FROM checkpoint_records WHERE workspace_id=? AND session_id=? ORDER BY created,checkpoint_id",
                          (self.workspace_id, self.session_id))
        return [self._snapshot_from_payload(row["payload"]) for row in rows]

    def has_snapshots(self) -> bool:
        return bool(self.get_snapshots())

    def snapshot(self, identifier: str | int) -> Snapshot:
        snapshots = self.get_snapshots()
        if isinstance(identifier, int):
            if 0 <= identifier < len(snapshots):
                return snapshots[identifier]
        else:
            matches = [s for s in snapshots if s.checkpoint_id == identifier or s.checkpoint_id.startswith(identifier)]
            if len(matches) == 1:
                return matches[0]
        raise RewindError("Checkpoint no longer exists or identifier is ambiguous")

    def prepare_edit(self, path: str, content: str, *, operation_id: str | None = None,
                     expected_content: str | None = None) -> str | None:
        """Publish before/expected-after blobs and commit intent before the write."""
        with self._lock:
            fp = validate_regular_path(path)
            if not self._protected(fp):
                self.note_uncovered(f"Project-external file edit (not covered by rewind): {fp}", entry_id=operation_id)
                return None
            if self.pending_restores():
                raise RewindError("An unfinished file restore requires /rewind resume before editing")
            if self._current_checkpoint is None:
                self.begin_checkpoint(0, "File edits (standalone task)")
            before = self._capture(str(fp))
            if expected_content is not None:
                previous_text = (self._read_backup(before) or b"").decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                if previous_text != expected_content:
                    raise RewindError("File changed while preparing its edit; read it again")
            after = self._content_backup(content, before)
            previous = self._rows("SELECT * FROM file_edit_records WHERE workspace_id=? AND path=? AND undone_by IS NULL ORDER BY seq DESC LIMIT 1",
                                  (self.workspace_id, str(fp)))
            barrier = bool(previous and not _same(before, Backup(**json.loads(previous[0]["after_state"])) ))
            # Keep the new external baseline reachable without allowing a rewind
            # to silently cross it, including two edits in the same task.
            if barrier:
                old = self.snapshot(self._current_checkpoint)
                self.begin_checkpoint(old.message_index, old.user_text + " (external baseline)",
                                      conversation=old.conversation, env_injected=old.env_injected,
                                      ltm_injected=old.ltm_injected)
            edit_id = uuid.uuid4().hex
            with self.store.transaction() as db:
                db.execute("""INSERT INTO file_edit_records(edit_id,workspace_id,session_id,generation,
                           checkpoint_id,operation_id,path,before_state,after_state,state,barrier,previous_seq,created)
                           VALUES(?,?,?,?,?,?,?,?,?,'prepared',?,?,?)""",
                           (edit_id, self.workspace_id, self.session_id, self.generation, self._current_checkpoint,
                            operation_id, str(fp), _dump(asdict(before)), _dump(asdict(after)), int(barrier),
                            previous[0]["seq"] if previous else 0, time.time()))
            return edit_id

    def verify_prepared(self, edit_id: str | None) -> None:
        if edit_id is None:
            return
        row = self._rows("SELECT * FROM file_edit_records WHERE edit_id=?", (edit_id,))[0]
        if not self._matches(Path(row["path"]), Backup(**json.loads(row["before_state"]))):
            raise RewindError("File changed after checkpoint preparation; write was not started")

    def applied_edit(self, edit_id: str | None) -> None:
        if edit_id is None:
            return
        row = self._rows("SELECT * FROM file_edit_records WHERE edit_id=?", (edit_id,))[0]
        if not self._matches(Path(row["path"]), Backup(**json.loads(row["after_state"]))):
            raise RewindError("Written file differs from its expected state; recovery inspection is required")
        with self.store.transaction() as db:
            db.execute("UPDATE file_edit_records SET state='applied' WHERE edit_id=?", (edit_id,))

    def track_edit(self, path: str) -> None:
        """Legacy embedding helper; the built-in tools use prepare_edit instead."""
        self._legacy_pending[str(Path(path).resolve())] = self._capture(path)

    def record_edit(self, path: str) -> None:
        path = str(Path(path).resolve())
        before = self._legacy_pending.pop(path)
        after = self._capture(path)
        if self._current_checkpoint is None:
            self.begin_checkpoint(0, "Legacy file edits")
        with self.store.transaction() as db:
            db.execute("""INSERT INTO file_edit_records(edit_id,workspace_id,session_id,generation,
                       checkpoint_id,path,before_state,after_state,state,created) VALUES(?,?,?,?,?,?,?,?,'applied',?)""",
                       (uuid.uuid4().hex, self.workspace_id, self.session_id, self.generation,
                        self._current_checkpoint, path, _dump(asdict(before)), _dump(asdict(after)), time.time()))

    @staticmethod
    def _matches(path: Path, backup: Backup) -> bool:
        try:
            validate_regular_path(path)
            if not backup.exists:
                return not path.exists()
            if not path.is_file():
                return False
            st = path.stat()
            if st.st_size > MAX_FILE_BYTES:
                return False
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            return len(data) <= MAX_FILE_BYTES and st.st_mode & 0o777 == backup.mode and hashlib.sha256(data).hexdigest() == backup.digest
        except OSError:
            return False

    def _read_backup(self, backup: Backup) -> bytes | None:
        if not backup.exists:
            return None
        try:
            return self.store.get_blob(backup.digest)
        except Exception as exc:
            raise RewindError("Checkpoint backup is missing, unreadable, or damaged; restore stopped") from exc

    def preview(self, identifier: str | int) -> RestorePreview:
        """Read-only: no file writes, decisions, or execution-state changes."""
        target = self.snapshot(identifier)
        result = RestorePreview(target.checkpoint_id, self.workspace_id)
        ancestors = set()
        cursor = self._current_checkpoint
        snapshots = {s.checkpoint_id: s for s in self.get_snapshots()}
        while cursor and cursor not in ancestors:
            ancestors.add(cursor)
            cursor = snapshots[cursor].parent_id if cursor in snapshots else None
        if target.checkpoint_id not in ancestors:
            result.conflicts.append("This checkpoint belongs to a previous branch; its immutable backups are retained for inspection.")
            return result
        rows = self._rows("SELECT * FROM file_edit_records WHERE workspace_id=? AND seq>? ORDER BY seq",
                          (self.workspace_id, target.baseline_seq))
        paths = sorted({r["path"] for r in rows if r["session_id"] == self.session_id and not r["undone_by"]})
        for path in paths:
            records = [r for r in rows if r["path"] == path]
            owned = [r for r in records if r["session_id"] == self.session_id and not r["undone_by"]]
            if not owned:
                continue
            if any(r["session_id"] != self.session_id and not r["undone_by"] for r in records):
                result.conflicts.append(f"{path}: contains edits owned by another session")
                continue
            if any(r["barrier"] and r["previous_seq"] > target.baseline_seq for r in records):
                result.conflicts.append(f"{path}: rewind crosses a recorded external edit boundary")
                continue
            desired = Backup(**json.loads(owned[0]["before_state"]))
            expected = Backup(**json.loads(owned[-1]["after_state"]))
            try:
                self._protected(Path(path))
                for record in owned:
                    self._read_backup(Backup(**json.loads(record["before_state"])))
                    self._read_backup(Backup(**json.loads(record["after_state"])))
                for left, right in zip(owned, owned[1:]):
                    if not _same(Backup(**json.loads(left["after_state"])), Backup(**json.loads(right["before_state"]))):
                        raise RewindError("rewind crosses a recorded external edit boundary")
                current = self._capture(path, persist=False)
                if not _same(current, expected):
                    # A prepared edit may never have published the target. Its
                    # before state is evidence of no content change, not proof
                    # about any related tool or Hook.
                    prepared_before = Backup(**json.loads(owned[-1]["before_state"]))
                    if owned[-1]["state"] == "prepared" and _same(current, prepared_before):
                        expected = prepared_before
                    else:
                        raise RewindError("file changed outside the agent")
                if _same(current, desired):
                    continue
                before_bytes = self._read_backup(expected) or b""
                target_bytes = self._read_backup(desired) or b""
                diff = "".join(difflib.unified_diff(before_bytes.decode("utf-8", "replace").splitlines(True),
                                                   target_bytes.decode("utf-8", "replace").splitlines(True),
                                                   fromfile=path + " (current)", tofile=path + " (checkpoint)"))
                result.files.append(RestoreFile(path, current, desired, [r["edit_id"] for r in owned], diff))
            except (OSError, RewindError) as exc:
                result.conflicts.append(f"{path}: {exc}")
        uncovered = self._rows("SELECT description FROM checkpoint_uncovered WHERE workspace_id=? AND session_id=? AND created>=? ORDER BY created",
                               (self.workspace_id, self.session_id, target.timestamp))
        result.uncovered = [r["description"] for r in uncovered]
        result.uncovered.append("Bash, MCP, Git operations and external editors are not covered by file checkpoints.")
        return result

    def start_restore(self, identifier: str | int, *, option: int = 3) -> str:
        self._validate_workspace()
        if option not in (1, 2, 3):
            raise RewindError("Unknown restore option")
        if self.pending_restores():
            raise RewindError("An unfinished restore exists; inspect and resume it first")
        target = self.snapshot(identifier)
        if option in (1, 2) and target.conversation is None:
            raise RewindError("Conversation snapshot unavailable; choose code only")
        preview = self.preview(target.checkpoint_id) if option in (1, 3) else RestorePreview(target.checkpoint_id, self.workspace_id)
        if preview.conflicts:
            raise RewindError("Restore refused; no files were restored: " + "; ".join(preview.conflicts))
        # Preserve a new independently verifiable pre-restore safety copy before
        # committing the restore intent. Never replace missing blobs with absence.
        for item in preview.files:
            safety = self._capture(item.path)
            if not _same(safety, item.before):
                raise RewindError(f"File changed outside the agent: {item.path}")
            item.before = safety
        restore_id = uuid.uuid4().hex
        with self.store.transaction() as db:
            db.execute("INSERT INTO file_restores VALUES(?,?,?,?,?,?,?,?,?)",
                       (restore_id, self.workspace_id, self.session_id, target.checkpoint_id, "prepared", option,
                        time.time(), f"restore:{restore_id}:conversation", _dump({"snapshot": asdict(target), "uncovered": preview.uncovered})))
            for position, item in enumerate(preview.files):
                db.execute("INSERT INTO file_restore_items VALUES(?,?,?,?,?,?,?)",
                           (restore_id, item.path, position, _dump(asdict(item.before)), _dump(asdict(item.target)),
                            _dump(item.edit_ids), "pending"))
        return restore_id

    def pending_restores(self) -> list[dict[str, Any]]:
        return self._rows("SELECT restore_id,checkpoint_id,state,option,created FROM file_restores WHERE workspace_id=? AND state!='complete' ORDER BY created",
                          (self.workspace_id,))

    def restore_info(self, restore_id: str) -> dict[str, Any]:
        rows = self._rows("SELECT * FROM file_restores WHERE restore_id=? AND workspace_id=? AND session_id=?",
                          (restore_id, self.workspace_id, self.session_id))
        if not rows:
            raise RewindError("Restore does not belong to this workspace and session")
        info = rows[0]
        info["items"] = self._rows("SELECT * FROM file_restore_items WHERE restore_id=? ORDER BY position", (restore_id,))
        for item in info["items"]:
            before = Backup(**json.loads(item["before_state"]))
            target = Backup(**json.loads(item["target_state"]))
            item["observed"] = "at_target" if self._matches(Path(item["path"]), target) else "not_restored" if self._matches(Path(item["path"]), before) else "conflict"
        return info

    def inspect_checkpoint(self, identifier: str | int) -> dict[str, Any]:
        """Expose retained immutable copies, including abandoned branches."""
        snapshot = self.snapshot(identifier)
        edits = self._rows("SELECT edit_id,path,before_state,after_state,state,undone_by FROM file_edit_records WHERE checkpoint_id=? ORDER BY seq",
                           (snapshot.checkpoint_id,))
        for edit in edits:
            edit["before"] = json.loads(edit.pop("before_state"))
            edit["after"] = json.loads(edit.pop("after_state"))
        return {"checkpoint_id": snapshot.checkpoint_id, "workspace_id": self.workspace_id,
                "baseline": {path: asdict(backup) for path, backup in snapshot.backups.items()},
                "edits": edits}

    def apply_restore(self, restore_id: str) -> list[str]:
        self._validate_workspace()
        info = self.restore_info(restore_id)
        if info["state"] == "complete":
            return []
        with ExitStack() as locks:
            for item in sorted(info["items"], key=lambda item: item["path"]):
                locks.enter_context(path_lock(Path(item["path"])))
            with self._lock:
                info = self.restore_info(restore_id)
                conflicts = [item["path"] for item in info["items"] if item["observed"] == "conflict"]
                if conflicts:
                    raise RewindError("Files changed outside the agent; restore stopped: " + ", ".join(conflicts))
                staged: dict[str, str | None] = {}
                try:
                    # Verify and stage the entire remaining set before touching
                    # the first live path. Safety copies remain in the blob store.
                    for item in info["items"]:
                        target = Backup(**json.loads(item["target_state"]))
                        self._read_backup(Backup(**json.loads(item["before_state"])))
                        data = self._read_backup(target)
                        if item["observed"] == "at_target":
                            continue
                        if data is None:
                            staged[item["path"]] = None
                        else:
                            parent = Path(item["path"]).parent
                            validate_regular_path(item["path"])
                            make_parents_durable(parent)
                            fd, temp = tempfile.mkstemp(prefix=".nanocursor-rewind-", dir=parent)
                            staged[item["path"]] = temp
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(data)
                                os.fchmod(stream.fileno(), target.mode if target.mode is not None else 0o600)
                                stream.flush()
                                os.fsync(stream.fileno())
                    for item in info["items"]:
                        path = item["path"]
                        target = Backup(**json.loads(item["target_state"]))
                        if not self._matches(Path(path), target):
                            if not self._matches(Path(path), Backup(**json.loads(item["before_state"]))):
                                raise RewindError(f"File changed during restore; some files may already be restored: {path}")
                            temp = staged[path]
                            if temp is None:
                                Path(path).unlink()
                            else:
                                os.replace(temp, path)
                            sync_directory(Path(path).parent)
                        with self.store.transaction() as db:
                            db.execute("UPDATE file_restore_items SET state='applied' WHERE restore_id=? AND path=?", (restore_id, path))
                    with self.store.transaction() as db:
                        db.execute("UPDATE file_restores SET state='files_done' WHERE restore_id=?", (restore_id,))
                except Exception as exc:
                    if isinstance(exc, RewindError):
                        raise
                    raise RewindError(f"Restore interrupted; inspect /rewind resume {restore_id}. Some files may already be restored: {exc}") from exc
                finally:
                    for temp in staged.values():
                        if temp:
                            Path(temp).unlink(missing_ok=True)
        return [item["path"] for item in info["items"]]

    def complete_restore(self, restore_id: str) -> None:
        info = self.restore_info(restore_id)
        if info["state"] == "complete":
            return
        if info["state"] != "files_done":
            raise RewindError("Restore files have not completed")
        with self.store.transaction() as db:
            for item in info["items"]:
                for edit_id in json.loads(item["edit_ids"]):
                    db.execute("UPDATE file_edit_records SET undone_by=? WHERE edit_id=? AND undone_by IS NULL", (restore_id, edit_id))
            db.execute("UPDATE file_restores SET state='complete' WHERE restore_id=?", (restore_id,))
            db.execute("INSERT OR REPLACE INTO checkpoint_heads VALUES(?,?,?)",
                       (self.workspace_id, self.session_id, info["checkpoint_id"]))
        self._current_checkpoint = info["checkpoint_id"]

    def rewind(self, snapshot_index: str | int) -> list[str]:
        """Programmatic, explicitly confirmed code-only restore convenience."""
        restore_id = self.start_restore(snapshot_index)
        changed = self.apply_restore(restore_id)
        self.complete_restore(restore_id)
        return changed

    def referenced_blobs(self) -> set[str]:
        references: set[str] = set()
        for table, columns in (("file_edit_records", ("before_state", "after_state")),
                               ("file_restore_items", ("before_state", "target_state"))):
            for row in self._rows(f"SELECT {','.join(columns)} FROM {table}"):
                for column in columns:
                    backup = json.loads(row[column])
                    if backup.get("exists"):
                        references.add(backup["digest"])
        for row in self._rows("SELECT payload FROM checkpoint_records"):
            for backup in json.loads(row["payload"])["backups"].values():
                if backup.get("exists"):
                    references.add(backup["digest"])
        return references

    def retention_info(self) -> dict[str, Any]:
        """Expose evidence retention; do not prune chains merely to meet a cap."""
        snapshots = self.get_snapshots()
        digests = self.referenced_blobs()
        size = 0
        for digest in digests:
            try:
                size += (self.store.blob_dir / digest).stat().st_size
            except OSError:
                pass
        return {"checkpoints": len(snapshots), "soft_limit": MAX_SNAPSHOTS,
                "pending_restores": self.pending_restores(), "referenced_blobs": len(self.referenced_blobs()),
                "referenced_bytes_all_workspaces": size,
                "reason": "Checkpoints with file edits or restore evidence are retained; only unreferenced checkpoints may be pruned."}

    def pin_checkpoint(self, identifier: str, *, pinned: bool = True) -> None:
        snapshot = self.snapshot(identifier)
        snapshot.pinned = pinned
        with self.store.transaction() as db:
            db.execute("UPDATE checkpoint_records SET pinned=?,payload=? WHERE checkpoint_id=?",
                       (int(pinned), _dump(asdict(snapshot)), snapshot.checkpoint_id))

    def prune_unreferenced(self, *, keep: int = MAX_SNAPSHOTS) -> int:
        """The cap is soft: pinned points and edit/restore references always win."""
        snapshots = self.get_snapshots()
        prunable = snapshots[:-keep] if keep > 0 else snapshots
        removed = 0
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM file_restores WHERE workspace_id=? AND state!='complete' LIMIT 1",
                          (self.workspace_id,)).fetchone():
                return 0
            if db.execute("""SELECT 1 FROM operations o WHERE workspace_id=? AND
                          (state='intent' OR (state='outcome_unknown' AND NOT EXISTS
                           (SELECT 1 FROM resolutions r WHERE r.operation_id=o.operation_id))) LIMIT 1""",
                          (self.workspace_id,)).fetchone():
                return 0
            for snapshot in prunable:
                eligible = db.execute("""SELECT parent_id FROM checkpoint_records WHERE checkpoint_id=? AND pinned=0
                    AND NOT EXISTS(SELECT 1 FROM file_edit_records WHERE checkpoint_id=?)
                    AND NOT EXISTS(SELECT 1 FROM file_restores WHERE checkpoint_id=?)
                    AND NOT EXISTS(SELECT 1 FROM checkpoint_heads WHERE checkpoint_id=?)
                    AND NOT EXISTS(SELECT 1 FROM runs WHERE runs.run_id=checkpoint_records.run_id AND state='running')""",
                    (snapshot.checkpoint_id, snapshot.checkpoint_id, snapshot.checkpoint_id,
                     snapshot.checkpoint_id)).fetchone()
                if not eligible:
                    continue
                parent_id = eligible[0]
                # Removing an empty point preserves the ancestry of its children.
                for row in db.execute("SELECT checkpoint_id,payload FROM checkpoint_records WHERE parent_id=?",
                                      (snapshot.checkpoint_id,)).fetchall():
                    child = json.loads(row[1])
                    child["parent_id"] = parent_id
                    db.execute("UPDATE checkpoint_records SET parent_id=?,payload=? WHERE checkpoint_id=?",
                               (parent_id, _dump(child), row[0]))
                cursor = db.execute("DELETE FROM checkpoint_records WHERE checkpoint_id=?", (snapshot.checkpoint_id,))
                removed += cursor.rowcount
        return removed
