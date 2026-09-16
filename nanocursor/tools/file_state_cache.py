from __future__ import annotations

from pathlib import Path

from nanocursor.tools.file_io import FileVersion, read_snapshot
from nanocursor.tools.runtime import current_runtime


class FileStateCache:
    """Versions belong to the observing agent, not a shared tool instance.

    Each model response uses a frozen expectation snapshot. Another write cannot
    bless a stale edit. Tools hold a path lock across compare-and-write; external
    editors do not participate in that lock.
    """

    def __init__(self) -> None:
        self._cache: dict[str, FileVersion] = {}

    def _entries(self) -> dict[str, FileVersion]:
        context = current_runtime()
        return context.file_versions if context is not None else self._cache

    def record_version(self, path: str, version: FileVersion) -> None:
        self._entries()[path] = version

    def record(self, path: str, content: str, mtime_ns: int) -> None:
        text, version = read_snapshot(Path(path))
        if text != content or version.stat[3] != mtime_ns:
            self._entries().pop(path, None)
            return
        self.record_version(path, version)

    def check(self, path: str) -> tuple[bool, str]:
        context = current_runtime()
        entries = context.expected_versions if context and context.expected_versions is not None else self._entries()
        expected = entries.get(path)
        if expected is None:
            return False, "Error: file has not been read yet. Read it first before editing."
        try:
            _, current = read_snapshot(Path(path))
        except OSError:
            return False, "Error: file changed or was deleted since last read. Read it again before editing."
        if current != expected:
            return False, "Error: file has been modified since last read. Read it again before editing."
        return True, ""

    def has_read(self, path: str) -> bool:
        return path in self._entries()

    def update(self, path: str) -> None:
        try:
            _, version = read_snapshot(Path(path))
            self.record_version(path, version)
        except OSError:
            self._entries().pop(path, None)
