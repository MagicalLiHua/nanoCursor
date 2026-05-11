"""
Task Pool Manager for the Supervisor-driven architecture.
Manages the DAG of tasks, dependency resolution, and task lifecycle.
"""

import time
from typing import Literal

from src.infra.schemas import Task, TaskStatus, TaskUpdate


class TaskPool:
    """
    Thread-safe task pool managing the DAG of tasks.

    The Supervisor uses this to:
    - Get runnable tasks (all dependencies met)
    - Complete or fail tasks
    - Add new sub-tasks during execution
    """

    def __init__(self, max_pool_size: int = 50):
        self._tasks: dict[str, Task] = {}
        self._completed: list[str] = []
        self._failed: list[str] = []
        self._max_pool_size = max_pool_size

    def add_task(self, task: Task) -> None:
        """Add a new task to the pool. Prunes oldest COMPLETED tasks if at capacity."""
        if len(self._tasks) >= self._max_pool_size:
            self._prune_completed(count=10)
        self._tasks[task.id] = task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_runnable_tasks(self) -> list[Task]:
        """
        Get all tasks whose dependencies are satisfied and status is PENDING or BLOCKED.
        These are the tasks the Supervisor can choose from.
        """
        runnable = []
        for task in self._tasks.values():
            if task.status not in (TaskStatus.PENDING, TaskStatus.BLOCKED):
                continue
            deps_met = all(
                dep_id in self._completed
                for dep_id in task.dependencies
            )
            if deps_met:
                runnable.append(task)
        return runnable

    def get_in_progress_task(self) -> Task | None:
        """Get the currently in-progress task, if any."""
        for task in self._tasks.values():
            if task.status == TaskStatus.IN_PROGRESS:
                return task
        return None

    def complete_task(self, task_id: str, result: str) -> None:
        """Mark a task as completed."""
        task = self._tasks.get(task_id)
        if task:
            task.mark_completed(result)
            if task_id not in self._completed:
                self._completed.append(task_id)
            # Unblock any tasks that were waiting on this one
            for t in self._tasks.values():
                if task_id in t.dependencies and t.status == TaskStatus.BLOCKED:
                    t.status = TaskStatus.PENDING

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed and block its dependents."""
        task = self._tasks.get(task_id)
        if task:
            task.mark_failed(error)
            if task_id not in self._failed:
                self._failed.append(task_id)
            # Mark dependent tasks as BLOCKED
            for t in self._tasks.values():
                if task_id in t.dependencies:
                    t.status = TaskStatus.BLOCKED

    def apply_update(self, update: TaskUpdate) -> None:
        """Apply a TaskUpdate from the Supervisor to the pool."""
        task = self._tasks.get(update.task_id)
        if task:
            task.status = update.status
            task.result = update.result
        for new_task in update.new_tasks:
            self.add_task(new_task)

    def _prune_completed(self, count: int) -> None:
        """Remove the oldest N completed tasks to make room."""
        removed = 0
        while removed < count and self._completed:
            oldest_id = self._completed.pop(0)
            if oldest_id in self._tasks:
                del self._tasks[oldest_id]
            removed += 1

    @property
    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    @property
    def completed_count(self) -> int:
        return len(self._completed)

    @property
    def failed_count(self) -> int:
        return len(self._failed)

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    def to_state_dict(self) -> dict:
        """Serialize the task pool for AgentState storage."""
        return {
            "tasks": [t.model_dump() for t in self._tasks.values()],
            "completed": self._completed.copy(),
            "failed": self._failed.copy(),
        }

    @classmethod
    def from_state_dict(cls, data: dict | None) -> "TaskPool":
        """Reconstruct a TaskPool from serialized state."""
        pool = cls()
        if not data:
            return pool
        for task_data in data.get("tasks", []):
            task = Task(**task_data)
            pool._tasks[task.id] = task
        pool._completed = data.get("completed", [])
        pool._failed = data.get("failed", [])
        return pool


def create_initial_tasks(execution_plan: dict) -> list[Task]:
    """
    Convert an ExecutionPlan (from Planner) into an initial TaskPool.

    Each step in the ExecutionPlan becomes a Task with sequential dependencies.
    """
    tasks = []
    steps = execution_plan.get("steps", [])

    for step in steps:
        step_id = step.get("id", len(tasks) + 1)
        task_id = f"task-{step_id:03d}"
        task = Task(
            id=task_id,
            description=step.get("description", ""),
            status=TaskStatus.PENDING,
            dependencies=[],
        )
        tasks.append(task)

    # Set sequential dependencies (each task depends on the previous one)
    for i, task in enumerate(tasks):
        if i > 0:
            task.dependencies = [tasks[i - 1].id]

    return tasks
