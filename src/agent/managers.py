"""Manager classes for todos, tasks, and background jobs.

Extracted from engine.py to reduce file size.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ========== Todo Manager ==========

@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


class TodoManager:
    def __init__(self, workdir: Path):
        self._todo_file = workdir / ".todos.json"
        self.items: list[TodoItem] = []
        self._load()

    def _load(self):
        if self._todo_file.exists():
            try:
                data = json.loads(self._todo_file.read_text(encoding="utf-8"))
                self.items = [TodoItem(**t) for t in data]
            except (json.JSONDecodeError, TypeError, OSError):
                self.items = []

    def _save(self):
        data = [{"id": t.id, "content": t.content, "status": t.status, "created_at": t.created_at} for t in self.items]
        self._todo_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def add(self, content: str) -> str:
        todo_id = str(int(time.time() * 1000))
        self.items.append(TodoItem(id=todo_id, content=content))
        self._save()
        return todo_id

    def update(self, todo_id: str, status: str):
        for t in self.items:
            if t.id == todo_id:
                t.status = status
                break
        self._save()

    def list_all(self) -> list[TodoItem]:
        return self.items


# ========== Task Manager ==========

class TaskManager:
    def __init__(self, tasks_dir: Path = None, workdir: Path = None):
        if tasks_dir is not None:
            self.tasks_dir = tasks_dir
        elif workdir is not None:
            self.tasks_dir = workdir / ".tasks"
        else:
            raise ValueError("Either tasks_dir or workdir must be provided")
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def _task_file(self, task_id: str) -> Path:
        return self.tasks_dir / f"task_{task_id}.json"

    def create(self, subject: str, description: str = "", blocked_by: list = None) -> dict:
        task_id = str(int(time.time() * 1000))
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blocked_by": blocked_by or [],
            "created_at": time.time(),
            "completed_at": None,
        }
        self._task_file(task_id).write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        return task

    def get(self, task_id: str) -> Optional[dict]:
        f = self._task_file(task_id)
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def update_status(self, task_id: str, status: str):
        task = self.get(task_id)
        if task:
            task["status"] = status
            if status == "completed":
                task["completed_at"] = time.time()
            self._task_file(task_id).write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

    def list_all(self) -> list[dict]:
        tasks = []
        for f in self.tasks_dir.glob("task_*.json"):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return sorted(tasks, key=lambda t: t.get("created_at", 0))

    def list_runnable(self) -> list[dict]:
        all_tasks = self.list_all()
        completed_ids = {t["id"] for t in all_tasks if t["status"] == "completed"}
        runnable = []
        for t in all_tasks:
            if t["status"] != "pending":
                continue
            blocked_by = t.get("blocked_by", [])
            if all(b in completed_ids for b in blocked_by):
                runnable.append(t)
        return runnable


# ========== Background Manager ==========

class BackgroundManager:
    def __init__(self, workdir: Path):
        self._workdir = workdir
        self._tasks: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def run(self, command: str, label: str = "") -> str:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "id": task_id, "command": command, "label": label,
            "status": "running", "started_at": time.time(), "result": None,
        }
        asyncio.create_task(self._run_background(task_id, command))
        return task_id

    async def _run_background(self, task_id: str, command: str):
        import subprocess
        try:
            r = subprocess.run(command, shell=True, cwd=str(self._workdir), capture_output=True, timeout=300)
            out = r.stdout.decode("gbk", errors="replace") + r.stderr.decode("gbk", errors="replace")
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["result"] = out.strip()
        except Exception as e:
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = str(e)

    def check(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def list_all(self) -> list[dict]:
        return list(self._tasks.values())
