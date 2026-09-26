"""Synchronous file transactions shared by the local file tools."""
from __future__ import annotations

import hashlib
import os
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


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
