"""
Pydantic schemas for nanoCursor.

保留 Task/TaskStatus/TaskUpdate 等类型，供 task_manager.py 和测试使用。
其他复杂的 schema 已移除（简化自原来的 SupervisorDecision 等）。
"""

from enum import Enum
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class Task(BaseModel):
    id: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = Field(default_factory=list)
    result: str | None = None
    error: str | None = None

    def mark_completed(self, result: str):
        self.status = TaskStatus.COMPLETED
        self.result = result

    def mark_failed(self, error: str):
        self.status = TaskStatus.FAILED
        self.error = error


class TaskUpdate(BaseModel):
    task_id: str
    status: TaskStatus | None = None
    result: str | None = None
    new_tasks: list[Task] = Field(default_factory=list)