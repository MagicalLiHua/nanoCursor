from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Worktree:
    name: str
    path: str
    branch: str
    based_on: str
    head_commit: str
    created: datetime = field(default_factory=datetime.now)
    workspace_id: str = ""
    creation_id: str = ""
    common_dir: str = ""
    git_dir: str = ""
    git_dir_identity: str = ""
    owner: str = ""
    state: str = "active"
    error: str = ""


@dataclass
class WorktreeSession:
    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str = ""
    hook_based: bool = False
    workspace_id: str = ""


@dataclass(frozen=True)
class RemovalApproval:
    """A short-lived challenge delivered only through the user's slash command."""

    token: str
    workspace_id: str
    fingerprint: str
    expires_at: float
    summary: str
