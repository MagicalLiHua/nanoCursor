"""Tests for cron gRPC client — requires go-cron running on localhost:50057."""

import os
import time
import pytest


def cron_available():
    try:
        from src.runtime.cron_client import health
        result = health()
        return result.get("ok", False)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not cron_available(), reason="go-cron not running")


class TestCronHealth:
    def test_health(self):
        from src.runtime.cron_client import health
        result = health()
        assert result["ok"] is True
        assert result["service"] == "nanocursor-cron"


class TestCronTasks:
    def test_create_and_list(self):
        from src.runtime.cron_client import create_task, list_tasks
        task = create_task("*/5 * * * *", "test task", recurring=True)
        assert task["id"] != ""
        assert task["prompt"] == "test task"
        assert task["recurring"] is True

        tasks = list_tasks()
        assert any(t["prompt"] == "test task" for t in tasks)

    def test_delete_task(self):
        from src.runtime.cron_client import create_task, delete_task, list_tasks
        task = create_task("*/5 * * * *", "to delete")
        result = delete_task(task["id"])
        assert result["success"] is True

        tasks = list_tasks()
        assert not any(t["id"] == task["id"] for t in tasks)

    def test_delete_nonexistent(self):
        from src.runtime.cron_client import delete_task
        result = delete_task("nonexistent_id")
        assert result["success"] is False
