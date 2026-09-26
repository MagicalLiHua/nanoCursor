"""Small, private, atomic user-state writes. Importing this module does no I/O."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from nanocursor.validator import ConfigError


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ConfigError(f"Application directory is a symbolic link: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ConfigError(f"Not a directory: {path}")


def read_private_file(path: Path, *, missing: bytes = b"") -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise ConfigError(f"Refusing symbolic-link state file: {path}")
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return missing
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ConfigError(f"Not a regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read()
    finally:
        os.close(fd)


def atomic_write(path: Path, content: bytes) -> None:
    private_directory(path.parent)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ConfigError(f"Refusing non-regular state file: {path}")
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(read_private_file(path, missing=b"{}").decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ConfigError(f"Invalid JSON in {path}; restore a backup or repair the file") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, data: dict) -> None:
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


@contextmanager
def state_lock(directory: Path):
    private_directory(directory)
    path = directory / ".settings.lock"
    if path.is_symlink():
        raise ConfigError("Settings lock must not be a symbolic link")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ConfigError("Settings lock must be a regular file")
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                os.write(fd, b"0")
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ConfigError("Another settings operation is running; retry after it finishes") from exc
        yield
    finally:
        os.close(fd)
