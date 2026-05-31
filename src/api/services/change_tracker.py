"""ChangeTracker: per-run file change tracking for sub-agent coordination."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class ChangeTracker:
    """Track file changes within a run for cross-agent awareness.

    Records which agent changed which file and when, persisted to a JSONL
    file so that later-spawned agents can see what earlier agents modified.
    """

    def __init__(self, thread_id: str, workspace_dir: str):
        self.thread_id = thread_id
        self.workspace_dir = workspace_dir
        self._changes_dir = Path(workspace_dir) / ".nanocursor" / "runs" / thread_id
        self._changes_file = self._changes_dir / "file_changes.jsonl"

    def record_change(
        self,
        file_path: str,
        agent_name: str,
        change_type: str = "modify",
    ) -> None:
        """Record a file change.

        Args:
            file_path: Repo-relative path of the changed file.
            agent_name: Name of the agent that made the change.
            change_type: One of "create", "modify", "delete".
        """
        entry = {
            "file": file_path,
            "agent": agent_name,
            "type": change_type,
            "timestamp": time.time(),
        }
        try:
            self._changes_dir.mkdir(parents=True, exist_ok=True)
            with open(self._changes_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def get_changes(self, exclude_agent: str | None = None) -> list[dict[str, Any]]:
        """Get all recorded changes, optionally excluding one agent's changes."""
        if not self._changes_file.exists():
            return []
        changes: list[dict[str, Any]] = []
        try:
            with open(self._changes_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if exclude_agent and entry.get("agent") == exclude_agent:
                        continue
                    changes.append(entry)
        except OSError:
            pass
        return changes

    def get_changed_files(self, exclude_agent: str | None = None) -> set[str]:
        """Get the set of file paths that have been changed."""
        return {c["file"] for c in self.get_changes(exclude_agent) if c.get("file")}

    def build_change_context(self, exclude_agent: str | None = None) -> str:
        """Build a compact change summary string for injection into agent prompts."""
        changes = self.get_changes(exclude_agent=exclude_agent)
        if not changes:
            return ""
        # Deduplicate by file, keep latest entry per file
        seen: dict[str, dict[str, Any]] = {}
        for c in changes:
            f = c.get("file", "")
            if f:
                seen[f] = c
        lines = ["## 当前 Run 已发生的文件变更", "以下文件在本轮中已被其他 Agent 修改，请注意接口一致性："]
        for f, c in list(seen.items())[:15]:
            agent = c.get("agent", "?")
            ctype = c.get("type", "modify")
            lines.append(f"- {f} ({ctype} by {agent})")
        return "\n".join(lines)
