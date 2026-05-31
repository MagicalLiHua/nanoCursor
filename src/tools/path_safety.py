"""Unified path safety check for all tool implementations."""

from __future__ import annotations

import os
from pathlib import Path


def safe_path(p: str, workdir: str | Path) -> Path:
    """Resolve a path relative to workdir and validate it doesn't escape.

    Args:
        p: Relative or absolute path string.
        workdir: The workspace root directory.

    Returns:
        Resolved absolute Path guaranteed to be inside workdir.

    Raises:
        ValueError: If the resolved path escapes the workspace.
    """
    root = Path(workdir).resolve()
    normalized = str(p).replace("\\", os.sep)
    path = (Path(normalized) if Path(normalized).is_absolute() else root / normalized).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {p}") from exc
    return path
