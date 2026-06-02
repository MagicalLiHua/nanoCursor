"""Mutable task-board scheduling primitives.

This module is deliberately model/runtime focused. It does not call an LLM by
itself; it only decides which task-board items are safe to run and applies task
results. The Agent loop remains the primary planner and can mutate the board.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.runtime.task_board import RunTask, RunTaskBoard


class TaskExecutionResult(BaseModel):
    task_id: str
    status: Literal["passed", "failed", "blocked", "skipped"]
    summary: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    failure_category: str | None = None
    retryable: bool = True

    @property
    def node_id(self) -> str:
        return self.task_id


class NodeExecutionResult(TaskExecutionResult):
    """Legacy alias that accepts the old ``node_id`` constructor field."""

    def __init__(self, **data: Any):
        if "task_id" not in data and "node_id" in data:
            data["task_id"] = data["node_id"]
        super().__init__(**data)


class ScheduleDecision(BaseModel):
    runnable: list[dict[str, Any]]
    blocked: list[dict[str, Any]]
    locks_in_use: list[str]
    parallel_limit: int


def preview_next_batch(board: RunTaskBoard, parallel_limit: int = 3) -> ScheduleDecision:
    """Return the next set of tasks that can run without resource conflicts."""
    ready = board.ready_nodes()
    locks_in_use = _locked_resources(board)
    acquired: set[str] = set()
    runnable: list[RunTask] = []
    blocked: list[dict[str, Any]] = []

    write_node_selected = False
    for task in ready:
        conflicts = [
            lock for lock in task.resource_locks
            if lock in locks_in_use or lock in acquired
        ]
        if conflicts:
            blocked.append({
                "task_id": task.id,
                "node_id": task.id,
                "reason": "resource_lock_conflict",
                "locks": conflicts,
            })
            continue
        if task.writes_files:
            if write_node_selected:
                blocked.append({
                    "task_id": task.id,
                    "node_id": task.id,
                    "reason": "write_node_serialized",
                    "locks": task.resource_locks,
                })
                continue
            write_node_selected = True
        if len(runnable) >= max(1, parallel_limit):
            blocked.append({
                "task_id": task.id,
                "node_id": task.id,
                "reason": "parallel_limit",
                "locks": task.resource_locks,
            })
            continue
        runnable.append(task)
        acquired.update(task.resource_locks)

    return ScheduleDecision(
        runnable=[_task_schedule_item(task) for task in runnable],
        blocked=blocked,
        locks_in_use=sorted(locks_in_use),
        parallel_limit=max(1, parallel_limit),
    )


def mark_task_running(board: RunTaskBoard, task_id: str) -> RunTaskBoard:
    """Mark a task running and acquire its resource locks."""
    task = board.node(task_id)
    if not task:
        raise ValueError(f"Run task not found: {task_id}")
    if task.status not in {"ready", "pending"}:
        raise ValueError(f"Run task {task_id} is not runnable: {task.status}")
    conflicts = [lock for lock in task.resource_locks if lock in _locked_resources(board)]
    if conflicts:
        raise ValueError(f"Run task {task_id} has locked resources: {', '.join(conflicts)}")
    task.status = "running"
    for lock_id in task.resource_locks:
        lock = next((item for item in board.resources if item.id == lock_id), None)
        if lock:
            lock.status = "locked"
            lock.owner_node_id = task_id
    board.status = "running"
    board.updated_at = time.time()
    board.record_change("task_started", {"node_id": task_id, "task_id": task_id})
    return board


def apply_task_result(board: RunTaskBoard, result: TaskExecutionResult) -> RunTaskBoard:
    """Apply a task execution result, release locks, and unlock dependents."""
    task = board.node(result.task_id)
    if not task:
        raise ValueError(f"Run task not found: {result.task_id}")
    task.status = result.status
    if result.summary:
        task.outputs.append({"kind": "summary", "content": result.summary, "created_at": time.time()})
    task.outputs.extend(result.outputs)
    task.evidence.extend(result.evidence)
    _release_task_locks(board, task.id)

    if result.status == "failed":
        for child in board.nodes:
            if task.id in child.dependencies and child.status in {"pending", "ready"}:
                child.status = "blocked"
        _maybe_add_recovery_task(board, task, result)
    else:
        board.ready_nodes()

    terminal = all(
        node.status in {"passed", "failed", "skipped", "cancelled"}
        for node in board.nodes
    )
    if terminal:
        board.status = "failed" if any(node.status == "failed" for node in board.nodes) else "completed"
    board.updated_at = time.time()
    board.record_change(
        "task_result",
        {
            "node_id": task.id,
            "task_id": task.id,
            "status": result.status,
            "failure_category": result.failure_category,
            "retryable": result.retryable,
        },
    )
    return board


def _maybe_add_recovery_task(board: RunTaskBoard, failed_task: RunTask, result: TaskExecutionResult) -> None:
    if not result.retryable:
        return
    if failed_task.retry_policy.retry_count >= failed_task.retry_policy.max_retries:
        return
    recovery_id = f"node-recovery-{failed_task.id}"
    if board.node(recovery_id):
        return
    failed_task.retry_policy.retry_count += 1
    board.add_or_update_node(
        RunTask(
            id=recovery_id,
            type="recovery",
            title=f"恢复：{failed_task.title}",
            goal=f"处理节点失败：{result.summary or result.failure_category or 'unknown failure'}",
            agent_role="lead",
            status="ready",
            dependencies=[],
            can_parallel=False,
            writes_files=False,
            context_policy={"mode": "failure", "failed_task_id": failed_task.id, "failed_node_id": failed_task.id},
            acceptance=[],
        ),
        reason="task_failed_recovery",
    )


def _locked_resources(board: RunTaskBoard) -> set[str]:
    return {
        lock.id
        for lock in board.resources
        if lock.status == "locked" and lock.owner_node_id
    }


def _release_task_locks(board: RunTaskBoard, task_id: str) -> None:
    for lock in board.resources:
        if lock.owner_node_id == task_id:
            lock.owner_node_id = None
            lock.status = "free"


def _task_schedule_item(task: RunTask) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "node_id": task.id,
        "type": task.type,
        "title": task.title,
        "agent_role": task.agent_role,
        "can_parallel": task.can_parallel,
        "writes_files": task.writes_files,
        "resource_locks": task.resource_locks,
        "context_policy": task.context_policy,
    }


mark_node_running = mark_task_running
apply_node_result = apply_task_result
_maybe_add_recovery_node = _maybe_add_recovery_task
_release_node_locks = _release_task_locks
_node_schedule_item = _task_schedule_item
