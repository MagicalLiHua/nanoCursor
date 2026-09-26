"""An invocation's workspace is independent of where nanoCursor is installed."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nanocursor.validator import ConfigError


@dataclass
class WorkspaceContext:
    launch_cwd: Path
    workspace_dir: Path
    active_cwd: Path
    restored: object | None = None

    @classmethod
    def resolve(cls, path: str | None = None) -> WorkspaceContext:
        launch = Path.cwd()
        selected = Path(path).expanduser() if path else launch
        selected = selected.resolve()
        if not selected.is_dir() or not os.access(selected, os.R_OK | os.X_OK):
            raise ConfigError(f"Workspace is not an accessible directory: {selected}")
        return cls(launch, selected, selected)

    @property
    def state_dir(self) -> Path:
        return self.workspace_dir / ".nanocursor"

    def previous_worktree(self):
        from nanocursor.worktree.manager import WorktreeManager
        from nanocursor.worktree.session import load_worktree_session
        try:
            session = load_worktree_session(self.state_dir)
        except (OSError, ValueError, TypeError):
            return None
        if session is None:
            return None
        if not isinstance(session.worktree_path, str) or not isinstance(session.worktree_name, str):
            return None
        from nanocursor.worktree.slug import validate_slug
        if validate_slug(session.worktree_name):
            return None
        if not isinstance(session.original_cwd, str) or Path(session.original_cwd).resolve() != self.workspace_dir:
            return None
        path = Path(session.worktree_path).resolve()
        # Session files are project data. A forged record must not relocate the
        # process to an arbitrary directory outside the managed worktree root.
        if not path.is_relative_to(self.state_dir / "worktrees"):
            return None
        if WorktreeManager.read_worktree_head_sha(str(path)) is None:
            return None
        return session

    def restore(self, session) -> None:
        checked = self.previous_worktree()
        if checked is None or checked != session:
            raise ConfigError("Worktree record changed during selection; restart and review it again")
        self.active_cwd = Path(session.worktree_path).resolve()
        self.restored = session
