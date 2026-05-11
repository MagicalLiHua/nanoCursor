"""
Tests for TaskPool and task management in the Supervisor-driven architecture.
"""

import pytest

from src.infra.schemas import Task, TaskStatus
from src.tasks.manager import TaskPool, create_initial_tasks


class TestTaskPool:
    def test_add_and_get(self):
        pool = TaskPool()
        task = Task(id="t1", description="test task")
        pool.add_task(task)
        assert pool.get_task("t1") == task
        assert pool.pending_count == 1

    def test_complete_task(self):
        pool = TaskPool()
        task = Task(id="t1", description="test")
        pool.add_task(task)
        pool.complete_task("t1", "done")
        assert pool.get_task("t1").status == TaskStatus.COMPLETED
        assert pool.get_task("t1").result == "done"
        assert pool.completed_count == 1

    def test_fail_task(self):
        pool = TaskPool()
        task = Task(id="t1", description="test")
        pool.add_task(task)
        pool.fail_task("t1", "error")
        assert pool.get_task("t1").status == TaskStatus.FAILED
        assert pool.failed_count == 1

    def test_runnable_requires_deps_met(self):
        pool = TaskPool()
        t1 = Task(id="t1", description="first")
        t2 = Task(id="t2", description="second", dependencies=["t1"])
        pool.add_task(t1)
        pool.add_task(t2)

        assert len(pool.get_runnable_tasks()) == 1  # Only t1
        pool.complete_task("t1", "done")
        assert len(pool.get_runnable_tasks()) == 1  # Now t2 is runnable

    def test_failed_blocks_dependents(self):
        pool = TaskPool()
        t1 = Task(id="t1", description="first")
        t2 = Task(id="t2", description="second", dependencies=["t1"])
        pool.add_task(t1)
        pool.add_task(t2)

        pool.fail_task("t1", "error")
        assert pool.get_task("t2").status == TaskStatus.BLOCKED

    def test_to_state_dict_and_back(self):
        pool = TaskPool()
        task = Task(id="t1", description="test")
        pool.add_task(task)
        pool.complete_task("t1", "done")

        state = pool.to_state_dict()
        restored = TaskPool.from_state_dict(state)

        assert restored.get_task("t1").status == TaskStatus.COMPLETED
        assert restored.completed_count == 1

    def test_from_state_dict_none(self):
        pool = TaskPool.from_state_dict(None)
        assert pool.pending_count == 0

    def test_get_in_progress_task(self):
        pool = TaskPool()
        t1 = Task(id="t1", description="first")
        t2 = Task(id="t2", description="second")
        pool.add_task(t1)
        pool.add_task(t2)
        t1.status = TaskStatus.IN_PROGRESS
        assert pool.get_in_progress_task().id == "t1"


class TestCreateInitialTasks:
    def test_empty_plan(self):
        tasks = create_initial_tasks({})
        assert tasks == []

    def test_single_step(self):
        plan = {"steps": [{"id": 1, "description": "step 1", "action": "read"}]}
        tasks = create_initial_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].id == "task-001"
        assert tasks[0].dependencies == []

    def test_multiple_steps_sequential_deps(self):
        plan = {
            "steps": [
                {"id": 1, "description": "step 1", "action": "read"},
                {"id": 2, "description": "step 2", "action": "write"},
            ]
        }
        tasks = create_initial_tasks(plan)
        assert len(tasks) == 2
        assert tasks[0].dependencies == []
        assert tasks[1].dependencies == ["task-001"]
