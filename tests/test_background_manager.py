"""Background manager runtime boundary tests."""

from __future__ import annotations

import asyncio

from src.agent.managers import BackgroundManager


def test_background_manager_runs_command_without_blocking_loop(tmp_path):
    async def scenario():
        manager = BackgroundManager(tmp_path)
        task_id = await manager.run("echo background", label="check")

        for _ in range(50):
            task = manager.check(task_id) or {}
            if task.get("status") != "running":
                return task
            await asyncio.sleep(0.01)
        return manager.check(task_id)

    result = asyncio.run(scenario())

    assert result is not None
    assert result["status"] == "completed"
    assert "background" in result["result"]
