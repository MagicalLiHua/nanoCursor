"""Shared filters for user-facing change evidence."""

from __future__ import annotations


_INTERNAL_PREFIXES = (
    ".nanocursor/",
    ".backups/",
    ".tasks/",
    ".snapshots/",
)

_GENERATED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".coverage",
    "node_modules",
    "dist",
    "build",
}

_GENERATED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".class",
    ".o",
    ".so",
    ".dll",
    ".dylib",
)


def normalize_change_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def is_internal_change_path(path: str) -> bool:
    normalized = normalize_change_path(path)
    return normalized == ".nanocursor" or normalized.startswith(_INTERNAL_PREFIXES)


def is_generated_change_path(path: str) -> bool:
    normalized = normalize_change_path(path)
    if not normalized:
        return False
    parts = set(normalized.split("/"))
    if parts & _GENERATED_DIRS:
        return True
    return normalized.endswith(_GENERATED_SUFFIXES)


def should_hide_change_path(path: str) -> bool:
    """Return true for files that should not appear in delivery diffs."""
    return is_internal_change_path(path) or is_generated_change_path(path)
