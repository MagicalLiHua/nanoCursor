"""Unified path guard — all workspace file I/O must pass through these functions.

Prevents path-traversal escapes (``../``, absolute-path escapes, Windows ``..\\``)
and ensures every filesystem operation stays inside the authorised workspace.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def resolve_workspace_path(
    workspace: str | Path,
    user_path: str,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a user-supplied relative/absolute path safely inside *workspace*.

    Raises:
        ValueError: if the resolved path escapes the workspace boundary.
        FileNotFoundError: if *must_exist* is True and the path does not exist.
    """
    ws = Path(workspace).expanduser().resolve()
    if not ws.is_dir():
        raise ValueError(f"Workspace 不存在或不是目录: {ws}")

    if not user_path or not user_path.strip():
        raise ValueError("路径不能为空。")

    candidate = Path(user_path).expanduser()

    # If absolute, it must already be inside the workspace
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (ws / candidate).resolve()

    # Primary check: resolved path starts with workspace
    try:
        resolved.relative_to(ws)
    except ValueError:
        raise ValueError(
            f"路径越界，不能访问工作区之外的文件: {user_path}"
        )

    # Secondary check: commonpath (handles symlink tricks)
    ws_real = ws.resolve()
    candidate_real = resolved.resolve()
    try:
        if os.path.commonpath([str(ws_real), str(candidate_real)]) != str(ws_real):
            raise ValueError(f"路径越界 (commonpath): {user_path}")
    except ValueError:
        raise ValueError(f"路径越界，不能访问工作区之外的文件: {user_path}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"文件不存在: {resolved}")

    return resolved


def assert_within_workspace(workspace: str | Path, path: str | Path) -> Path:
    """Assert *path* is inside *workspace*. Returns the resolved Path or raises ValueError."""
    return resolve_workspace_path(workspace, str(path))


def safe_relative_to_workspace(workspace: str | Path, path: str | Path) -> str:
    """Return *path* as a POSIX-style relative path inside *workspace*.

    Returns the original path string if resolution fails (best-effort).
    """
    try:
        ws = Path(workspace).expanduser().resolve()
        p = Path(path).expanduser().resolve()
        rel = p.relative_to(ws)
        return rel.as_posix()
    except (ValueError, OSError):
        return str(path)


def safe_slug(value: str, max_length: int = 80) -> str:
    """Turn *value* into a filesystem-safe slug.

    Rejects or normalises ``/``, ``\\``, ``..``, and any character that isn't
    alphanumeric, dash, or underscore.
    """
    raw = str(value or "").strip()
    # Replace path separators and traversal sequences
    raw = raw.replace("\\", "-").replace("/", "-")
    raw = raw.replace("..", "-")
    # Keep only alphanumeric, dash, underscore, dot
    raw = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw)
    # Collapse consecutive dashes and trim
    raw = re.sub(r"-{2,}", "-", raw).strip("-.")
    if not raw:
        raise ValueError(f"无法从输入生成有效的 slug: {value!r}")
    return raw[:max_length]
