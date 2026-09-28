"""Synchronous file transactions shared by the local file tools."""
from __future__ import annotations

import hashlib
import os
import stat as stat_module
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()
MAX_FILE_BYTES = 8 * 1024 * 1024


def read_bounded_text(path: Path, *, errors: str = "strict") -> str:
    """Bound the actual read too, since the file can grow after stat()."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise OSError(f"File exceeds the {MAX_FILE_BYTES} byte read limit; select a smaller file or extract a section with Bash")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise OSError(f"File exceeds the {MAX_FILE_BYTES} byte read limit")
    # Match Path.read_text's universal-newline behavior used by file versions.
    return data.decode("utf-8", errors=errors).replace("\r\n", "\n").replace("\r", "\n")


def path_lock(path: Path) -> Any:
    with _guard:
        return _locks.setdefault(str(path), threading.RLock())


def file_history_context(fallback: Any = None) -> tuple[Any, str | None]:
    """Use the executing workspace's history, not a shared tool's old cwd."""
    from nanocursor.recovery import current_runtime, RecoveryStorageError
    runtime = current_runtime()
    if runtime is None:
        return fallback, None
    history = runtime.file_history
    if history is None or history.workspace_id != runtime.workspace_id:
        raise RecoveryStorageError("No checkpoint service is bound to this executing workspace")
    return history, runtime.current_operation_id


def stamp(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass(frozen=True)
class FileVersion:
    stat: tuple[int, ...]
    digest: str


def read_snapshot(path: Path, cache: Any = None) -> tuple[str, FileVersion]:
    for _ in range(3):
        before = stamp(path)
        text = cache.get(str(path), before) if cache is not None else None
        if text is None:
            text = read_bounded_text(path)
        after = stamp(path)
        if before == after:
            if cache is not None:
                cache.put(str(path), text, after)
            return text, FileVersion(after, hashlib.sha256(text.encode()).hexdigest())
    raise OSError("File changed while being read; retry the read")


def validate_regular_path(raw_path: str | Path, *, cwd: Path | None = None) -> Path:
    """Validate the lexical path before resolve() can hide a symlink.

    The check is deliberately repeated immediately before publication. It is
    conflict detection, not a sandbox against a concurrently malicious writer.
    """
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    # macOS exposes its system temporary directories through root-owned
    # aliases. Normalize only these exact OS aliases; project-created links
    # still pass through the strict lexical walk below.
    if sys.platform == "darwin" and len(path.parts) > 1 and path.parts[1] in {"var", "tmp"}:
        alias = Path("/") / path.parts[1]
        expected = Path("/private") / path.parts[1]
        try:
            metadata = alias.lstat()
            if stat_module.S_ISLNK(metadata.st_mode) and metadata.st_uid == 0 and alias.resolve() == expected:
                path = expected.joinpath(*path.parts[2:])
        except OSError:
            pass
    for component in (path, *path.parents):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat_module.S_ISLNK(mode):
            raise OSError(f"File editing does not support symbolic links: {component}")
        if component == path:
            st = component.stat()
            if not stat_module.S_ISREG(mode):
                raise OSError(f"File editing requires a regular file: {path}")
            if st.st_nlink != 1:
                raise OSError(f"File editing does not support hard links: {path}")
        elif not stat_module.S_ISDIR(mode):
            raise OSError(f"File parent is not a directory: {component}")
    return path.resolve()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_parents_durable(path: Path) -> None:
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        sync_directory(directory.parent)


def atomic_write(path: Path, content: str) -> None:
    validate_regular_path(path)
    make_parents_durable(path.parent)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            if path.exists():
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            os.fsync(stream.fileno())
        validate_regular_path(path)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
