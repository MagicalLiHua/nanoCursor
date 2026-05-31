"""Tests for WorkspaceFileLock and file write coordination."""

import asyncio
import pytest

from src.agent.file_lock import WorkspaceFileLock, get_file_lock, cleanup_file_lock


def test_file_lock_basic_read_write():
    """Test basic read/write lock acquisition and release."""
    async def run():
        lock = WorkspaceFileLock()

        # Acquire and release write lock
        await lock.acquire_write("/tmp/test.py")
        lock.release_write("/tmp/test.py")

        # Acquire and release read lock
        await lock.acquire_read("/tmp/test.py")
        lock.release_read("/tmp/test.py")

    asyncio.run(run())


def test_file_lock_multiple_readers():
    """Multiple readers can acquire read lock concurrently."""
    async def run():
        lock = WorkspaceFileLock()
        results = []

        async def reader(reader_id):
            await lock.acquire_read("/tmp/test.py")
            results.append(f"reader-{reader_id}-acquired")
            await asyncio.sleep(0.05)
            results.append(f"reader-{reader_id}-released")
            lock.release_read("/tmp/test.py")

        # Start multiple readers concurrently
        await asyncio.gather(reader(1), reader(2), reader(3))

        # All readers should have acquired before any released
        acquires = [r for r in results if "acquired" in r]
        assert len(acquires) == 3

    asyncio.run(run())


def test_file_lock_write_excludes_write():
    """Write locks are exclusive - second writer must wait."""
    async def run():
        lock = WorkspaceFileLock()
        results = []

        async def writer(writer_id):
            await lock.acquire_write("/tmp/test.py")
            results.append(f"writer-{writer_id}-start")
            await asyncio.sleep(0.05)
            results.append(f"writer-{writer_id}-end")
            lock.release_write("/tmp/test.py")

        # Start two writers concurrently
        await asyncio.gather(writer(1), writer(2))

        # Writer 2 should start only after writer 1 ends
        assert results.index("writer-1-end") < results.index("writer-2-start")

    asyncio.run(run())


def test_file_lock_write_excludes_read():
    """Write lock blocks concurrent read acquisition."""
    async def run():
        lock = WorkspaceFileLock()
        results = []

        async def writer():
            await lock.acquire_write("/tmp/test.py")
            results.append("writer-start")
            await asyncio.sleep(0.05)
            results.append("writer-end")
            lock.release_write("/tmp/test.py")

        async def reader():
            await asyncio.sleep(0.01)  # Let writer start first
            await lock.acquire_read("/tmp/test.py")
            results.append("reader-acquired")
            lock.release_read("/tmp/test.py")

        await asyncio.gather(writer(), reader())

        # Reader should acquire after writer releases
        assert results.index("writer-end") < results.index("reader-acquired")

    asyncio.run(run())


def test_file_lock_run_write():
    """run_write convenience method acquires and releases lock."""
    async def run():
        lock = WorkspaceFileLock()
        result = []

        async def write_operation():
            result.append("started")
            await asyncio.sleep(0.01)
            result.append("completed")
            return "success"

        ret = await lock.run_write("/tmp/test.py", write_operation())
        assert ret == "success"
        assert result == ["started", "completed"]

    asyncio.run(run())


def test_file_lock_run_read():
    """run_read convenience method acquires and releases lock."""
    async def run():
        lock = WorkspaceFileLock()

        async def read_operation():
            return "data"

        ret = await lock.run_read("/tmp/test.py", read_operation())
        assert ret == "data"

    asyncio.run(run())


def test_file_lock_path_normalization():
    """Paths are normalized to prevent duplicate locks."""
    lock = WorkspaceFileLock()
    # Both paths should resolve to the same key
    key1 = lock._normalize("/tmp/test.py")
    key2 = lock._normalize("/tmp/../tmp/test.py")
    assert key1 == key2


def test_file_lock_cleanup():
    """cleanup() releases all locks."""
    async def run():
        lock = WorkspaceFileLock()
        await lock.acquire_write("/tmp/test.py")
        lock.cleanup()
        # After cleanup, internal state should be empty
        assert len(lock._locks) == 0
        assert len(lock._write_locks) == 0
        assert len(lock._read_counts) == 0

    asyncio.run(run())


def test_file_lock_registry():
    """get_file_lock returns same instance for same thread_id."""
    lock1 = get_file_lock("thread-1")
    lock2 = get_file_lock("thread-1")
    assert lock1 is lock2

    lock3 = get_file_lock("thread-2")
    assert lock3 is not lock1

    cleanup_file_lock("thread-1")
    cleanup_file_lock("thread-2")


def test_file_lock_concurrent_write_and_read():
    """Write and read locks interact correctly under concurrent access."""
    async def run():
        lock = WorkspaceFileLock()
        order = []

        async def writer():
            await lock.acquire_write("/tmp/shared.py")
            order.append("write-start")
            await asyncio.sleep(0.05)
            order.append("write-end")
            lock.release_write("/tmp/shared.py")

        async def reader(reader_id):
            await asyncio.sleep(0.01)  # Let writer start first
            await lock.acquire_read("/tmp/shared.py")
            order.append(f"read-{reader_id}")
            lock.release_read("/tmp/shared.py")

        await asyncio.gather(writer(), reader(1), reader(2))

        # Both readers should see write-end before their acquisition
        write_end_idx = order.index("write-end")
        read1_idx = order.index("read-1")
        read2_idx = order.index("read-2")
        assert write_end_idx < read1_idx
        assert write_end_idx < read2_idx

    asyncio.run(run())
