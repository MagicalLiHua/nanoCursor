"""
Background Manager - 借鉴 s13_background_tasks.py

后台任务管理器：
- 慢命令在后台线程运行
- 结果持久化到 .runtime-tasks/
- 完成通知注入队列，在每轮 LLM 调用前 drain
"""

import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from src.infra.config import WORKSPACE_DIR
WORKDIR = Path(WORKSPACE_DIR)


TASKS_DIR = WORKDIR / ".runtime-tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)

# 配置
STALL_THRESHOLD_S = 45
POLL_INTERVAL = 2


class BackgroundManager:
    """
    后台任务管理器 - 追踪运行中的后台任务
    """

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._notifications: queue.Queue = queue.PriorityQueue()

    def run(self, command: str, label: str = "") -> str:
        """
        在后台线程运行命令，立即返回 task_id。
        """
        task_id = str(uuid.uuid4())

        task_info = {
            "id": task_id,
            "command": command,
            "label": label,
            "status": "running",
            "started_at": time.time(),
            "result": None,
        }

        with self._lock:
            self._tasks[task_id] = task_info

        # 持久化任务记录
        self._persist_task(task_info)

        # 启动后台线程
        t = threading.Thread(
            target=self._run_background,
            args=(task_id, command),
            daemon=True,
        )
        t.start()

        return task_id

    def _run_background(self, task_id: str, command: str):
        """后台线程执行"""
        import subprocess

        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=WORKDIR,
                capture_output=True,
                timeout=300,
            )
            try:
                out = r.stdout.decode("gbk", errors="replace") + r.stderr.decode("gbk", errors="replace")
            except:
                out = (r.stdout or b"") + (r.stderr or b"")
                if isinstance(out, bytes):
                    out = out.decode("utf-8", errors="replace")

            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["result"] = out.strip()
                    self._tasks[task_id]["completed_at"] = time.time()
                    self._persist_task(self._tasks[task_id])

            # 发送通知
            self._notifications.put((0, {
                "type": "background_complete",
                "task_id": task_id,
                "status": "completed",
                "result": out.strip()[:500],
            }))

        except subprocess.TimeoutExpired:
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "stalled"
                    self._tasks[task_id]["error"] = "Timeout (300s)"
                    self._persist_task(self._tasks[task_id])
            self._notifications.put((1, {
                "type": "background_complete",
                "task_id": task_id,
                "status": "stalled",
            }))

        except Exception as e:
            with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = str(e)
                    self._persist_task(self._tasks[task_id])
            self._notifications.put((1, {
                "type": "background_complete",
                "task_id": task_id,
                "status": "failed",
                "error": str(e),
            }))

    def check(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self) -> list[dict]:
        """列出所有任务"""
        with self._lock:
            return list(self._tasks.values())

    def detect_stalled(self) -> list[str]:
        """检测超时任务"""
        stalled = []
        now = time.time()
        with self._lock:
            for task_id, info in self._tasks.items():
                if info["status"] == "running" and (now - info["started_at"]) > STALL_THRESHOLD_S:
                    stalled.append(task_id)
        return stalled

    def drain_notifications(self) -> list[dict]:
        """排出所有待处理通知"""
        notifications = []
        while True:
            try:
                _, notification = self._notifications.get_nowait()
                notifications.append(notification)
            except queue.Empty:
                break
        return notifications

    def _persist_task(self, task_info: dict):
        """持久化任务到磁盘"""
        task_file = TASKS_DIR / f"{task_info['id']}.json"
        import json
        task_file.write_text(json.dumps(task_info, ensure_ascii=False), encoding="utf-8")


# 全局单例
_bg_manager: Optional[BackgroundManager] = None


def get_background_manager() -> BackgroundManager:
    global _bg_manager
    if _bg_manager is None:
        _bg_manager = BackgroundManager()
    return _bg_manager


__all__ = ["BackgroundManager", "get_background_manager", "STALL_THRESHOLD_S"]