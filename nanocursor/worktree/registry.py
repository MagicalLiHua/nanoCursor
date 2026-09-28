"""Durable worktree creation facts, stored outside any removable checkout."""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nanocursor.worktree.models import Worktree


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class WorktreeRegistry:
    def __init__(self, common_dir: str, directory: Path | None = None, store=None) -> None:
        from nanocursor.recovery.store import RecoveryStore

        project_key = hashlib.sha256(common_dir.encode()).hexdigest()
        self.store = store if store is not None else RecoveryStore(directory)
        self.directory = self.store.lock_dir / ("worktree-" + project_key)
        self.kind = "worktree:" + project_key

    def save(self, wt: Worktree) -> None:
        if not wt.creation_id or not wt.creation_id.isalnum():
            raise ValueError("Missing or invalid worktree creation identity")
        data = asdict(wt)
        data["created"] = wt.created.isoformat()
        self.store.put_metadata(self.kind, wt.creation_id, {"schema": 1, "worktree": data})

    def load(self) -> list[Worktree]:
        records = []
        for data in self.store.list_metadata(self.kind):
            if data.get("schema") != 1:
                raise ValueError("Unsupported worktree registry schema")
            fields = dict(data["worktree"])
            fields["created"] = datetime.fromisoformat(fields["created"])
            wt = Worktree(**fields)
            records.append(wt)
        return records
