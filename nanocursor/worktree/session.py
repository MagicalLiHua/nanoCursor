from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from nanocursor.storage import read_json, write_json
from nanocursor.validator import ConfigError
from nanocursor.worktree.models import WorktreeSession
from nanocursor.worktree.registry import sync_directory

log = logging.getLogger(__name__)

SESSION_FILENAME = "worktree_session.json"


def _session_path(nanocursor_dir: Path) -> Path:
    return nanocursor_dir / SESSION_FILENAME


def save_worktree_session(
    nanocursor_dir: Path,
    session: WorktreeSession | None,
) -> None:
    path = _session_path(nanocursor_dir)
    if session is None:
        # 对齐 Go：传入 nil 时直接删除文件而非写空 JSON，
        # 避免遗留无意义的空文件。文件不存在时静默忽略。
        path.unlink(missing_ok=True)
        if path.parent.exists():
            sync_directory(path.parent)
        return
    write_json(path, asdict(session))
    sync_directory(path.parent)


def load_worktree_session(nanocursor_dir: Path) -> WorktreeSession | None:
    path = _session_path(nanocursor_dir)
    if not path.exists():
        return None
    try:
        data = read_json(path)
        if not data or "worktree_path" not in data:
            return None
        return WorktreeSession(
            original_cwd=data["original_cwd"],
            worktree_path=data["worktree_path"],
            worktree_name=data["worktree_name"],
            original_branch=data["original_branch"],
            original_head_commit=data["original_head_commit"],
            session_id=data.get("session_id", ""),
            hook_based=data.get("hook_based", False),
            workspace_id=data.get("workspace_id", ""),
        )
    except (ValueError, KeyError, ConfigError) as e:
        log.warning("Failed to load worktree session: %s", e)
        return None
