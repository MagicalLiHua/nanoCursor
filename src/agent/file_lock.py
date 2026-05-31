"""Async file-level locks for concurrent agent write coordination.

Prevents multiple agents from writing to the same file simultaneously.
Uses asyncio.Lock per file path, with read/write distinction.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


class WorkspaceFileLock:
    """Per-file read/write lock for concurrent agent coordination.

    - Multiple readers can hold the lock simultaneously.
    - Writers get exclusive access.
    - Paths are normalized to prevent duplicate locks.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._read_counts: dict[str, int] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _normalize(self, path: str) -> str:
        """Normalize path to a consistent string key."""
        return str(Path(path).resolve())

    def _get_lock(self, path: str) -> asyncio.Lock:
        key = self._normalize(path)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _get_write_lock(self, path: str) -> asyncio.Lock:
        key = self._normalize(path)
        if key not in self._write_locks:
            self._write_locks[key] = asyncio.Lock()
        return self._write_locks[key]

    async def acquire_read(self, path: str):
        """Acquire a read lock. Multiple readers allowed concurrently."""
        lock = self._get_lock(path)
        await lock.acquire()
        key = self._normalize(path)
        self._read_counts[key] = self._read_counts.get(key, 0) + 1
        lock.release()

    async def acquire_write(self, path: str):
        """Acquire a write lock. Exclusive access - blocks until no readers or writers."""
        write_lock = self._get_write_lock(path)
        await write_lock.acquire()
        lock = self._get_lock(path)
        await lock.acquire()

    def release_write(self, path: str):
        """Release a write lock."""
        key = self._normalize(path)
        lock = self._locks.get(key)
        if lock:
            lock.release()
        write_lock = self._write_locks.get(key)
        if write_lock:
            write_lock.release()

    def release_read(self, path: str):
        """Release a read lock."""
        key = self._normalize(path)
        count = self._read_counts.get(key, 0)
        if count > 0:
            self._read_counts[key] = count - 1

    async def run_write(self, path: str, coro):
        """Run a coroutine with write lock held."""
        await self.acquire_write(path)
        try:
            return await coro
        finally:
            self.release_write(path)

    async def run_read(self, path: str, coro):
        """Run a coroutine with read lock held."""
        await self.acquire_read(path)
        try:
            return await coro
        finally:
            self.release_read(path)

    def cleanup(self):
        """Release all locks."""
        self._locks.clear()
        self._read_counts.clear()
        self._write_locks.clear()


# ========== Global lock per thread ==========

_file_locks: dict[str, WorkspaceFileLock] = {}


def get_file_lock(thread_id: str) -> WorkspaceFileLock:
    """Get or create the file lock for a run thread."""
    if thread_id not in _file_locks:
        _file_locks[thread_id] = WorkspaceFileLock()
    return _file_locks[thread_id]


def cleanup_file_lock(thread_id: str):
    """Clean up file lock for a completed run."""
    lock = _file_locks.pop(thread_id, None)
    if lock:
        lock.cleanup()


__all__ = ["WorkspaceFileLock", "get_file_lock", "cleanup_file_lock"]
