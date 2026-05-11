"""
Worktree Manager - 借鉴 s18_worktree_task_isolation.py

Git worktree 隔离管理：
- 创建/管理/删除 worktree
- 任务绑定到 worktree lanes
- EventBus 记录所有生命周期事件为 JSONL
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from src.infra.config import WORKSPACE_DIR
WORKDIR = Path(WORKSPACE_DIR)


WORKTREES_DIR = WORKDIR / ".worktrees"
EVENTS_FILE = WORKTREES_DIR / "events.jsonl"
INDEX_FILE = WORKTREES_DIR / "index.json"

WORKTREES_DIR.mkdir(parents=True, exist_ok=True)


class EventBus:
    """追加式事件日志"""

    @staticmethod
    def emit(event: str, task_id: str = "", wt_name: str = "", **extra):
        """发射事件到 events.jsonl"""
        entry = {
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "task_id": task_id,
            "wt_name": wt_name,
            **extra,
        }
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def list_recent(limit: int = 10) -> list[dict]:
        """读取最近的事件"""
        if not EVENTS_FILE.exists():
            return []
        lines = EVENTS_FILE.read_text(encoding="utf-8").strip().split("\n")
        events = []
        for line in lines[-limit:]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return events


class WorktreeManager:
    """
    Git worktree 隔离管理器
    """

    def __init__(self):
        self._index: dict[str, dict] = {}
        self._load_index()

    def _load_index(self):
        """加载索引"""
        if INDEX_FILE.exists():
            try:
                self._index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._index = {}

    def _save_index(self):
        """保存索引"""
        INDEX_FILE.write_text(json.dumps(self._index, ensure_ascii=False), encoding="utf-8")

    def _git_available(self) -> bool:
        """检查 git 是否可用"""
        try:
            subprocess.run(
                ["git", "status"],
                cwd=WORKDIR,
                capture_output=True,
                timeout=5,
            )
            return True
        except Exception:
            return False

    def create(self, name: str, task_id: str = "", base_ref: str = "HEAD") -> dict | None:
        """
        创建新的 worktree。
        """
        if not self._git_available():
            return None

        wt_path = WORKTREES_DIR / name

        try:
            result = subprocess.run(
                ["git", "worktree", "add", str(wt_path), base_ref],
                cwd=WORKDIR,
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                entry = {
                    "name": name,
                    "path": str(wt_path),
                    "task_id": task_id,
                    "base_ref": base_ref,
                    "created_at": datetime.now().isoformat(),
                    "status": "active",
                }
                self._index[name] = entry
                self._save_index()
                EventBus.emit("worktree_created", task_id, name)
                return entry
        except Exception as e:
            EventBus.emit("worktree_create_failed", task_id, name, error=str(e))

        return None

    def list_all(self) -> list[dict]:
        """列出所有 worktree"""
        return list(self._index.values())

    def status(self, name: str) -> str:
        """获取 worktree 状态"""
        return self._index.get(name, {}).get("status", "unknown")

    def remove(self, name: str, force: bool = False) -> bool:
        """
        删除 worktree。
        """
        if name not in self._index:
            return False

        entry = self._index[name]
        wt_path = Path(entry["path"])

        # 尝试删除 worktree
        try:
            cmd = ["git", "worktree", "remove", str(wt_path)]
            if force:
                cmd.append("--force")
            subprocess.run(cmd, cwd=WORKDIR, capture_output=True, timeout=30)
        except Exception:
            pass

        # 从索引中移除
        del self._index[name]
        self._save_index()
        EventBus.emit("worktree_removed", entry.get("task_id", ""), name)
        return True

    def closeout(self, name: str, action: str = "keep") -> bool:
        """
        关闭 worktree：keep 或 remove。
        """
        if action == "keep":
            EventBus.emit("worktree_kept", self._index.get(name, {}).get("task_id", ""), name)
            return True
        return self.remove(name, force=True)


# 全局单例
_wt_manager: Optional[WorktreeManager] = None


def get_worktree_manager() -> WorktreeManager:
    global _wt_manager
    if _wt_manager is None:
        _wt_manager = WorktreeManager()
    return _wt_manager


__all__ = ["WorktreeManager", "EventBus", "get_worktree_manager"]