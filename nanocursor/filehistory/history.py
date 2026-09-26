from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from nanocursor.conversation import Message
from nanocursor.tools.file_io import path_lock

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


class FileHistory:
    """In-run checkpoints of successful file tools, taken at completed turns.

    Initial states cover paths first edited after an older checkpoint. Backups
    are immutable; missing backup data is an error, never an absence marker.
    """

    def __init__(self, base_dir: str, session_id: str) -> None:
        self._session_dir = Path(base_dir) / ".nanocursor" / "file-history" / session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._initial: dict[str, Backup] = {}
        self._expected: dict[str, Backup] = {}
        self._snapshots: list[Snapshot] = []
        self._lock = threading.RLock()

    def _capture(self, path: str) -> Backup:
        fp = Path(path)
        try:
            data = fp.read_bytes()
            mode = fp.stat().st_mode & 0o777
        except FileNotFoundError:
            return Backup("", 0, time.time(), exists=False)
        digest = hashlib.sha256(data).hexdigest()
        bp = self._session_dir / uuid.uuid4().hex
        bp.write_bytes(data)
        return Backup(str(bp), 0, time.time(), digest=digest, mode=mode)

    def track_edit(self, path: str) -> None:
        with self._lock:
            path = str(Path(path).resolve())
            if path not in self._initial:
                before = self._capture(path)
                self._initial[path] = before
                self._expected[path] = before

    def record_edit(self, path: str) -> None:
        """Called immediately after a successful write, under the file lock."""
        with self._lock:
            path = str(Path(path).resolve())
            self._expected[path] = self._capture(path)

    def make_snapshot(self, msg_index: int, user_text: str, *, conversation: list[Message] | None = None,
                      env_injected: bool = False, ltm_injected: bool = False) -> None:
        with self._lock:
            self._snapshots.append(Snapshot(
                message_index=msg_index, user_text=user_text,
                backups=dict(self._expected), timestamp=time.time(),
                conversation=copy.deepcopy(conversation),
                env_injected=env_injected, ltm_injected=ltm_injected,
            ))
            self._snapshots = self._snapshots[-MAX_SNAPSHOTS:]

    def get_snapshots(self) -> list[Snapshot]:
        with self._lock:
            return list(self._snapshots)

    def has_snapshots(self) -> bool:
        with self._lock:
            return bool(self._snapshots)

    @staticmethod
    def _matches(path: Path, backup: Backup) -> bool:
        # Never follow a newly introduced symlink while restoring old paths.
        if path.is_symlink() or path.resolve() != path:
            return False
        if not backup.exists:
            return not path.exists()
        try:
            return (path.is_file() and path.stat().st_mode & 0o777 == backup.mode
                    and hashlib.sha256(path.read_bytes()).hexdigest() == backup.digest)
        except OSError:
            return False

    @staticmethod
    def _read_backup(backup: Backup) -> bytes | None:
        if not backup.exists:
            return None
        try:
            data = Path(backup.backup_path).read_bytes()
        except OSError as exc:
            raise RewindError("Checkpoint backup is missing or unreadable; no files were restored") from exc
        if hashlib.sha256(data).hexdigest() != backup.digest:
            raise RewindError("Checkpoint backup is damaged; no files were restored")
        return data

    def rewind(self, snapshot_index: int) -> list[str]:
        # Use the same lock order as WriteFile/EditFile: path, then history.
        with ExitStack() as locks:
            for path in sorted(self._initial):
                locks.enter_context(path_lock(Path(path)))
            with self._lock:
                if not 0 <= snapshot_index < len(self._snapshots):
                    raise RewindError("Checkpoint no longer exists")
                target = self._snapshots[snapshot_index]
                desired = {p: target.backups.get(p, initial) for p, initial in self._initial.items()}
                changed = [p for p, backup in desired.items() if not self._matches(Path(p), backup)]
                conflicts = [p for p in changed if not self._matches(Path(p), self._expected[p])]
                if conflicts:
                    raise RewindError("Files changed outside the agent; no files were restored: " + ", ".join(conflicts))
                # Stage every target before changing any live path.
                staged: dict[str, str | None] = {}
                try:
                    for path in changed:
                        data = self._read_backup(desired[path])
                        if data is None:
                            staged[path] = None
                        else:
                            fd, temp = tempfile.mkstemp(prefix=".nanocursor-rewind-", dir=Path(path).parent)
                            staged[path] = temp
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(data)
                                os.fchmod(stream.fileno(), desired[path].mode if desired[path].mode is not None else 0o600)
                    for path, temp in staged.items():
                        if temp is None:
                            Path(path).unlink(missing_ok=True)
                        else:
                            os.replace(temp, path)
                        # Record partial success too; no multi-file atomicity claim.
                        self._expected[path] = desired[path]
                except OSError as exc:
                    raise RewindError(f"File restore failed; some files may already be restored: {exc}") from exc
                finally:
                    for temp in staged.values():
                        if temp:
                            Path(temp).unlink(missing_ok=True)
                self._expected.update(desired)
                self._snapshots = self._snapshots[:snapshot_index + 1]
                return changed
