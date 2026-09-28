"""Explicit, bounded setup; ordinary worktrees share no writable dependencies."""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path


def _local_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError(f"Worktree setup path must stay within its checkout: {relative}")
    target = root / path
    for candidate in (target, *target.parents):
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError(f"Worktree setup refuses linked paths: {relative}")
    return target


def perform_post_creation_setup(
    repo_root: str,
    wt_path: str,
    symlink_directories: list[str] | None = None,
) -> None:
    root, wt = Path(repo_root), Path(wt_path)
    # Sharing is retained only for an explicit user config. Never edit Git config:
    # it is shared with the source checkout by default.
    for relative in symlink_directories or []:
        source = _local_path(root, relative)
        target = _local_path(wt, relative)
        if source.is_dir() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source, target)
    _copy_ignored_files(root, wt)


def _copy_ignored_files(root: Path, wt: Path) -> None:
    include = root / ".worktreeinclude"
    if not include.exists():
        return
    _local_path(root, ".worktreeinclude")
    patterns = [line.strip() for line in include.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if not patterns:
        return
    result = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, timeout=30, check=True,
    )
    for relative in filter(None, result.stdout.split("\0")):
        if not any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
            continue
        source = _local_path(root, relative)
        target = _local_path(wt, relative)
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
