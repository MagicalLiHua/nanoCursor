"""Bounded memory storage with directory-relative, no-follow I/O.

The index is the publication point once migrated. Superseded bodies remain on
 disk, but every automatic reader uses the same active set from MEMORY.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote, unquote

ENTRYPOINT_NAME = "MEMORY.md"
INDEX_MARKER = "<!-- nanocursor-memory-index: 1 -->"
CHECKPOINT_PREFIX = "<!-- nanocursor-consolidation: "
MAX_FILE_BYTES = 256_000
MAX_FILES = 500
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_LINK_RE = re.compile(r"^- \[([^\]\n]*)\]\(([^)\n]+)\)(?:[ \t]+—[ \t]*(.*))?[ \t]*$")


class MemoryStorageError(OSError):
    """Storage is unsafe or damaged; callers must not silently publish."""


class MemoryPublishedError(MemoryStorageError):
    """Index replacement completed, but the durability confirmation failed."""


class MemoryConflict(MemoryStorageError):
    """A snapshot changed while the model was working."""


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def memory_filename(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or name.upper() == "MEMORY":
        raise MemoryStorageError("Memory names must be 1–80 ASCII letters, digits, '-' or '_'; MEMORY is reserved")
    return name + ".md"


def _components(relative: str) -> tuple[str, ...]:
    parts = tuple(relative.split("/"))
    if (not parts or any(not p or p in {".", ".."} for p in parts)
            or "\\" in relative or any(ord(c) < 32 or ord(c) == 127 for c in relative)):
        raise MemoryStorageError("Invalid relative memory path")
    return parts


def _index_target(relative: str) -> str:
    _components(relative)
    if not relative.endswith(".md") or Path(relative).name.upper() == ENTRYPOINT_NAME.upper():
        raise MemoryStorageError("Memory index contains an unsupported target")
    return relative


@dataclass(frozen=True)
class MemoryRecord:
    filename: str
    content: str
    mtime_ms: int

    @property
    def content_id(self) -> str:
        return digest(self.filename + "\0" + self.content)


@dataclass(frozen=True)
class MemorySnapshot:
    index: str
    records: tuple[MemoryRecord, ...]

    @property
    def version(self) -> str:
        return digest(self.index)

    @property
    def managed(self) -> bool:
        return INDEX_MARKER in self.index.splitlines()


@dataclass(frozen=True)
class MemoryCatalog:
    index: str
    # Metadata is a cache invalidation hint, never authorization to read a path.
    entries: tuple[tuple[str, tuple[int, int, int]], ...]

    @property
    def version(self) -> str:
        return digest(self.index)


class _Directory:
    def __init__(self, fd: int) -> None:
        self.fd = fd

    @contextmanager
    def parent(self, relative: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
        parts = _components(relative)
        fd = os.dup(self.fd)
        try:
            for component in parts[:-1]:
                if create:
                    try:
                        os.mkdir(component, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def read(self, relative: str, *, missing: str | None = None, prefix_bytes: int | None = None) -> str:
        try:
            with self.parent(relative) as (parent, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                    raise MemoryStorageError("Memory must be a bounded regular file")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1 if prefix_bytes is None else min(prefix_bytes, MAX_FILE_BYTES))
                if len(data) > MAX_FILE_BYTES:
                    raise MemoryStorageError("Memory exceeds the size limit")
                if prefix_bytes is not None:
                    import codecs
                    return codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
                return data.decode("utf-8")
            finally:
                os.close(fd)
        except FileNotFoundError:
            if missing is not None:
                return missing
            raise
        except UnicodeError as exc:
            raise MemoryStorageError("Memory is not valid UTF-8") from exc

    def exists(self, relative: str) -> bool:
        try:
            with self.parent(relative) as (parent, name):
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def mtime(self, relative: str) -> int:
        with self.parent(relative) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise MemoryStorageError("Memory is not a regular file")
            return int(info.st_mtime * 1000)

    def fingerprint(self, relative: str) -> tuple[int, int, int]:
        with self.parent(relative) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise MemoryStorageError("Memory must be a bounded regular file")
            return info.st_ino, info.st_mtime_ns, info.st_size

    def write(self, relative: str, content: str, *, new: bool = False) -> None:
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise MemoryStorageError("Memory exceeds the size limit")
        with self.parent(relative) as (parent, name):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None and (not stat.S_ISREG(info.st_mode) or new):
                raise MemoryStorageError("Refusing to replace a non-regular or reserved memory file")
            temporary = ".memory-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if new:
                    # An independently created target must not be overwritten.
                    os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(temporary, dir_fd=parent)
                else:
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                try:
                    os.fsync(parent)
                except OSError as exc:
                    if name == ENTRYPOINT_NAME:
                        raise MemoryPublishedError("Memory index was published, but directory fsync failed") from exc
                    raise
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def files(self) -> list[str]:
        found: list[str] = []
        def visit(fd: int, prefix: str, depth: int) -> None:
            if depth > 8:
                raise MemoryStorageError("Memory directory nesting exceeds the limit")
            for name in sorted(os.listdir(fd)):
                if name.startswith(".") and not name.endswith(".md"):
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                relative = prefix + name
                if stat.S_ISLNK(info.st_mode):
                    raise MemoryStorageError("Symbolic links are not allowed in memory storage")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        visit(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                elif name.endswith(".md") and name != ENTRYPOINT_NAME:
                    _index_target(relative)
                    if not stat.S_ISREG(info.st_mode):
                        raise MemoryStorageError("Memory is not a regular file")
                    found.append(relative)
                    if len(found) > MAX_FILES:
                        raise MemoryStorageError("Too many memory files; explicit maintenance is required")
        visit(self.fd, "", 0)
        return found

    @contextmanager
    def lock(self, name: str = ".memory.lock") -> Iterator[None]:
        import fcntl
        fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=self.fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise MemoryStorageError("Memory lock is not a regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MemoryConflict("Another memory operation is running") from exc
            yield
        finally:
            os.close(fd)


def _links(index: str) -> dict[str, int]:
    lines = index.splitlines()
    markers = [line for line in lines if "nanocursor-memory-index:" in line]
    if markers and markers != [INDEX_MARKER]:
        raise MemoryStorageError("Memory index version is invalid; repair it before continuing")
    targets: dict[str, int] = {}
    for i, line in enumerate(lines):
        match = _LINK_RE.fullmatch(line)
        if match:
            try:
                target = _index_target(unquote(match.group(2), errors="strict"))
            except UnicodeError as exc:
                raise MemoryStorageError("Memory index contains an invalid encoded filename") from exc
            if target in targets:
                raise MemoryStorageError("Memory index has duplicate targets")
            targets[target] = i
        elif line.lstrip().startswith("- ["):
            raise MemoryStorageError("Memory index has a malformed entry; repair it before continuing")
    if len(targets) > MAX_FILES:
        raise MemoryStorageError("Memory index exceeds the entry limit")
    return targets


def _pointer(filename: str, title: str, description: str) -> str:
    # These are display fields, never paths or Markdown supplied by the model.
    clean = lambda s: " ".join(str(s).replace("[", "(").replace("]", ")").split())
    target = quote(filename, safe="/-._~")
    return f"- [{clean(title)[:100]}]({target}) — {clean(description)[:200]}"


def render_memory(name: str, description: str, memory_type: str, body: str) -> str:
    if memory_type not in {"user", "feedback", "project", "reference"}:
        raise MemoryStorageError("Invalid memory type")
    return ("---\nname: " + json.dumps(name, ensure_ascii=False)
            + "\ndescription: " + json.dumps(description, ensure_ascii=False)
            + "\ntype: " + memory_type + "\n---\n\n" + body.strip() + "\n")


class MemoryStore:
    def __init__(self, trust_root: Path, relative: str) -> None:
        self.trust_root = trust_root.expanduser().resolve()
        self.relative = relative
        _components(relative)
        self.path = self.trust_root.joinpath(*_components(relative))

    @classmethod
    def from_directory(cls, directory: str | Path) -> "MemoryStore":
        path = Path(os.path.abspath(directory))
        # .nanocursor is managed, not an additional trust root. Do not resolve it.
        for ancestor in (path, *path.parents):
            if ancestor.name == ".nanocursor":
                return cls(ancestor.parent, str(path.relative_to(ancestor.parent)))
        return cls(path.parent, path.name)

    @contextmanager
    def open(self, *, create: bool = False) -> Iterator[_Directory]:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise MemoryStorageError("Safe memory storage requires POSIX no-follow directory operations")
        if create:
            self.trust_root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.trust_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in _components(self.relative):
                if create:
                    try:
                        os.mkdir(component, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield _Directory(fd)
        finally:
            os.close(fd)

    def _snapshot(self, directory: _Directory) -> MemorySnapshot:
        index = directory.read(ENTRYPOINT_NAME, missing="")
        if INDEX_MARKER not in index.splitlines() and (directory.exists(".consolidation-state.json")
                or any(line.startswith(CHECKPOINT_PREFIX) for line in index.splitlines())):
            raise MemoryStorageError("Migrated memory index lost its version marker; restore the index before continuing")
        links = _links(index)
        names = sorted(links) if INDEX_MARKER in index.splitlines() else directory.files()
        records = tuple(MemoryRecord(name, directory.read(name), directory.mtime(name)) for name in names)
        # An index with a missing target is damaged even before migration.
        for name in links:
            if name not in names:
                directory.read(name)
        return MemorySnapshot(index, records)

    def snapshot(self) -> MemorySnapshot:
        try:
            with self.open() as directory:
                return self._snapshot(directory)
        except FileNotFoundError:
            if self.path.exists():
                raise MemoryStorageError("Memory index references a missing file")
            return MemorySnapshot("", ())

    def read_index(self) -> str:
        # Checkpoints are persistence metadata, not prompt contents.
        return "\n".join(line for line in self.snapshot().index.splitlines()
                         if not line.startswith(CHECKPOINT_PREFIX))

    def catalog(self) -> MemoryCatalog:
        """Validate the active set without reading every memory body."""
        try:
            with self.open() as directory:
                index = directory.read(ENTRYPOINT_NAME, missing="")
                if INDEX_MARKER not in index.splitlines() and (
                    directory.exists(".consolidation-state.json")
                    or any(line.startswith(CHECKPOINT_PREFIX) for line in index.splitlines())
                ):
                    raise MemoryStorageError("Migrated memory index lost its version marker")
                links = _links(index)
                names = sorted(links) if INDEX_MARKER in index.splitlines() else directory.files()
                if len(names) > MAX_FILES:
                    raise MemoryStorageError("Too many active memories")
                entries = tuple((name, directory.fingerprint(name)) for name in names)
                for name in links:
                    if name not in names:
                        directory.fingerprint(name)
                return MemoryCatalog(index, entries)
        except FileNotFoundError:
            if self.path.exists():
                raise MemoryStorageError("Memory index references a missing file")
            return MemoryCatalog("", ())

    def read_catalog_files(self, catalog: MemoryCatalog, names: list[str], *,
                           prefix_bytes: int | None = None,
                           check: Callable[[], None] | None = None) -> dict[str, str]:
        """Read only selected active files; reject concurrent publication/edits."""
        current = self.catalog()
        if current != catalog:
            raise MemoryConflict("Memory catalog changed during recall")
        fingerprints = dict(catalog.entries)
        if any(name not in fingerprints for name in names):
            raise MemoryStorageError("Memory is no longer active")
        if not names:
            return {}
        with self.open() as directory:
            result = {}
            for name in names:
                if check:
                    check()
                result[name] = directory.read(name, prefix_bytes=prefix_bytes)
                if directory.fingerprint(name) != fingerprints[name]:
                    raise MemoryConflict("Memory changed during recall")
            if directory.read(ENTRYPOINT_NAME, missing="") != catalog.index:
                raise MemoryConflict("Memory index changed during recall")
            return result

    def checkpoint(self, snapshot: MemorySnapshot) -> dict | None:
        lines = [line for line in snapshot.index.splitlines() if line.startswith(CHECKPOINT_PREFIX)]
        if not lines:
            return None
        if len(lines) != 1 or not lines[0].endswith(" -->"):
            raise MemoryStorageError("Consolidation checkpoint is damaged")
        try:
            value = json.loads(lines[0][len(CHECKPOINT_PREFIX):-4])
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError as exc:
            raise MemoryStorageError("Consolidation checkpoint is damaged") from exc

    def read_active(self, filename: str) -> str:
        _index_target(filename)
        snapshot = self.snapshot()
        for record in snapshot.records:
            if record.filename == filename:
                return record.content
        raise MemoryStorageError("Memory is no longer active")

    def migrate(self) -> MemorySnapshot:
        """Publish the one-time active set and its version marker together."""
        with self.open(create=True) as directory, directory.lock():
            snapshot = self._snapshot(directory)
            if snapshot.managed:
                return snapshot
            links = _links(snapshot.index)
            lines = snapshot.index.rstrip("\n").splitlines()
            for record in snapshot.records:
                if record.filename not in links:
                    lines.append(_pointer(record.filename, Path(record.filename).stem, "Imported memory"))
            new_index = "\n".join([INDEX_MARKER, *lines]) + "\n"
            # Keep the original index outside the active memory directory.
            backup = MemoryStore(self.trust_root, self.relative + "-history")
            with backup.open(create=True) as archive:
                archive.write("index-before-migration-" + uuid.uuid4().hex + ".txt",
                              snapshot.index, new=True)
            directory.write(ENTRYPOINT_NAME, new_index)
            return MemorySnapshot(new_index, snapshot.records)

    @staticmethod
    def _check_snapshot(directory: _Directory, snapshot: MemorySnapshot,
                        sources: tuple[MemoryRecord, ...] | None = None) -> None:
        if directory.read(ENTRYPOINT_NAME, missing="") != snapshot.index:
            raise MemoryConflict("Memory index changed while the model was working")
        for record in sources if sources is not None else snapshot.records:
            try:
                current = directory.read(record.filename)
            except FileNotFoundError as exc:
                raise MemoryConflict("Memory source was removed while the model was working") from exc
            if current != record.content:
                raise MemoryConflict("Memory source changed while the model was working")

    def write_memories(self, memories: list[tuple[str, str, str, str]], snapshot: MemorySnapshot) -> None:
        """Publish validated extraction entries, rejecting snapshot conflicts."""
        names = [memory_filename(item[0]) for item in memories]
        if len(names) != len(set(names)):
            raise MemoryStorageError("Duplicate extracted memory names")
        with self.open(create=True) as directory, directory.lock():
            self._check_snapshot(directory, snapshot)
            known = {record.filename for record in snapshot.records}
            for name in names:
                if name not in known and directory.exists(name):
                    raise MemoryConflict("Memory target already exists outside the captured active set")
            lines = snapshot.index.rstrip("\n").splitlines()
            links = _links(snapshot.index)
            for name, (title, memory_type, description, body) in zip(names, memories):
                directory.write(name, render_memory(title, description, memory_type, body), new=name not in known)
                pointer = _pointer(name, title, description)
                if name in links:
                    lines[links[name]] = pointer
                else:
                    lines.append(pointer)
            directory.write(ENTRYPOINT_NAME, "\n".join(lines) + "\n")

    def publish(self, snapshot: MemorySnapshot, groups: list[dict], *, permitted=lambda: True,
                checkpoint=None) -> str:
        """Write new bodies, then publish the index. Old bodies are never changed."""
        if not snapshot.managed:
            raise MemoryStorageError("Consolidation requires an explicitly migrated index")
        by_id = {record.content_id: record for record in snapshot.records}
        with self.open() as directory, directory.lock():
            self._check_snapshot(directory, snapshot)
            if not permitted():
                raise MemoryConflict("Memory consolidation was disabled")
            retired = {by_id[source].filename for group in groups for source in group["sources"]}
            lines = snapshot.index.splitlines()
            links = _links(snapshot.index)
            lines = [line for i, line in enumerate(lines) if i not in {links[name] for name in retired}]
            replacements = []
            for group in groups:
                name = "consolidated-" + uuid.uuid4().hex + ".md"
                content = render_memory(group["name"], group["description"], group["type"], group["body"])
                directory.write(name, content, new=True)
                replacements.append(MemoryRecord(name, content, directory.mtime(name)))
                lines.append(_pointer(name, group["name"], group["description"]))
            self._check_snapshot(directory, snapshot)
            if not permitted():
                raise MemoryConflict("Memory consolidation was disabled")
            if checkpoint is not None:
                active = tuple(record for record in snapshot.records if record.filename not in retired) + tuple(replacements)
                receipt = checkpoint(active)
                lines = [line for line in lines if not line.startswith(CHECKPOINT_PREFIX)]
                # The checkpoint and active set share the same atomic publication.
                lines.append(CHECKPOINT_PREFIX + json.dumps(receipt, ensure_ascii=True, separators=(",", ":")) + " -->")
            new_index = "\n".join(lines) + "\n"
            directory.write(ENTRYPOINT_NAME, new_index)
            return digest(new_index)

    def clear(self) -> None:
        try:
            with self.open() as directory, directory.lock():
                for name in directory.files() + [ENTRYPOINT_NAME]:
                    with directory.parent(name) as (parent, basename):
                        try:
                            os.unlink(basename, dir_fd=parent)
                        except FileNotFoundError:
                            pass
                for name in (".consolidation-state.json",):
                    try:
                        os.unlink(name, dir_fd=directory.fd)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            return


def active_records(directory: str | Path) -> tuple[MemoryRecord, ...]:
    return MemoryStore.from_directory(directory).snapshot().records
