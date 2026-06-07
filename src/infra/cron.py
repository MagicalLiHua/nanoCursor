"""
Cron Scheduler - 借鉴 s14_cron_scheduler.py

定时任务调度器：
- 支持 cron 表达式（5字段）
- 后台线程每秒检查
- 触发时将通知注入队列
- durable 任务持久化到 .claude/scheduled_tasks.json
"""

import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from src.infra.config import WORKSPACE_DIR
WORKDIR = Path(WORKSPACE_DIR)


SCHEDULED_TASKS_FILE = WORKDIR / ".claude" / "scheduled_tasks.json"
SCHEDULED_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)

# 退避抖动配置
JITTER_MIN = 60
JITTER_MAX = 240


def cron_matches(expr: str, dt: datetime | None = None) -> bool:
    """检查 cron 表达式是否匹配给定时间"""
    if dt is None:
        dt = datetime.now()

    parts = expr.split()
    if len(parts) != 5:
        return False

    minute, hour, day, month, dow = parts

    return (
        _field_matches(minute, dt.minute, 0, 59) and
        _field_matches(hour, dt.hour, 0, 23) and
        _field_matches(day, dt.day, 1, 31) and
        _field_matches(month, dt.month, 1, 12) and
        _field_matches(dow, dt.weekday(), 0, 6)
    )


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    """检查单个字段是否匹配"""
    if field == "*":
        return True

    if "/" in field:
        base_str, step_str = field.split("/", 1)
        base = int(base_str) if base_str != "*" else 0
        return (value - base) % int(step_str) == 0

    if "-" in field:
        start, end = field.split("-")
        return int(start) <= value <= int(end)

    if "," in field:
        return value in [int(x) for x in field.split(",")]

    return int(field) == value


class CronLock:
    """PID 文件锁，防止重复触发"""

    def __init__(self, name: str):
        self.lock_file = WORKDIR / ".claude" / f".cron_lock_{name}"
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

    def acquire(self) -> bool:
        """获取锁，返回是否成功"""
        if self.lock_file.exists():
            pid = self.lock_file.read_text().strip()
            # 检查进程是否存活
            try:
                os.kill(int(pid), 0)
                return False  # 进程仍在运行
            except (OSError, ValueError):
                pass  # 进程已死，可以获取锁
        self.lock_file.write_text(str(os.getpid()))
        return True

    def release(self):
        """释放锁"""
        self.lock_file.unlink(missing_ok=True)


class CronTask:
    def __init__(
        self,
        task_id: str,
        cron_expr: str,
        prompt: str,
        recurring: bool = False,
        durable: bool = True,
    ):
        self.id = task_id
        self.cron_expr = cron_expr
        self.prompt = prompt
        self.recurring = recurring
        self.durable = durable
        self.created_at = time.time()
        self.last_fired_at: float | None = None
        self.next_fire_at: float | None = None


class CronScheduler:
    """
    定时任务调度器
    """

    def __init__(self):
        self._tasks: dict[str, CronTask] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._notifications: list[dict] = []

    def start(self):
        """启动调度器后台线程"""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        """后台主循环"""
        while self._running:
            now = time.time()
            next_check = now + 1

            with self._lock:
                for task_id, task in list(self._tasks.items()):
                    # 计算下次触发时间
                    if cron_matches(task.cron_expr):
                        if task.last_fired_at is None or (now - task.last_fired_at) > 60:
                            task.last_fired_at = now
                            self._notifications.append({
                                "type": "cron_fired",
                                "task_id": task_id,
                                "prompt": task.prompt,
                                "recurring": task.recurring,
                            })
                            # 非周期性任务完成后删除
                            if not task.recurring:
                                del self._tasks[task_id]
                    else:
                        # 计算下次精确触发时间（简化版：每分钟检查）
                        pass

            # 周期性任务每分钟再次检查
            time.sleep(1)

    def create(
        self,
        cron_expr: str,
        prompt: str,
        recurring: bool = False,
        durable: bool = True,
    ) -> str:
        """创建定时任务"""
        import uuid
        task_id = str(uuid.uuid4())

        task = CronTask(
            task_id=task_id,
            cron_expr=cron_expr,
            prompt=prompt,
            recurring=recurring,
            durable=durable,
        )

        with self._lock:
            self._tasks[task_id] = task

        if durable:
            self._persist_tasks()

        return task_id

    def delete(self, task_id: str) -> bool:
        """删除任务"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                if self._tasks.durable:
                    self._persist_tasks()
                return True
        return False

    def list_all(self) -> list[dict]:
        """列出所有任务"""
        with self._lock:
            return [
                {
                    "id": t.id,
                    "cron_expr": t.cron_expr,
                    "prompt": t.prompt,
                    "recurring": t.recurring,
                    "last_fired_at": t.last_fired_at,
                }
                for t in self._tasks.values()
            ]

    def drain_notifications(self) -> list[dict]:
        """排出所有待处理通知"""
        with self._lock:
            notifications = self._notifications.copy()
            self._notifications.clear()
        return notifications

    def detect_missed_tasks(self) -> list[dict]:
        """检测漏掉的任务（会话关闭期间应该触发的）"""
        missed = []
        for task in self._tasks.values():
            if task.last_fired_at and task.last_fired_at > time.time() - 86400:
                # 检查是否在最近24小时内有漏掉的任务
                pass  # 简化实现
        return missed

    def _persist_tasks(self):
        """持久化任务到磁盘"""
        data = [
            {
                "id": t.id,
                "cron_expr": t.cron_expr,
                "prompt": t.prompt,
                "recurring": t.recurring,
                "last_fired_at": t.last_fired_at,
            }
            for t in self._tasks.values() if t.durable
        ]
        import json
        SCHEDULED_TASKS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# 全局单例
_cron_scheduler: Optional[CronScheduler] = None


def get_cron_scheduler() -> CronScheduler:
    global _cron_scheduler
    if _cron_scheduler is None:
        _cron_scheduler = CronScheduler()
    return _cron_scheduler


__all__ = ["CronScheduler", "CronTask", "get_cron_scheduler", "cron_matches"]