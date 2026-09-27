"""The task command must distinguish cancellation requests from task completion."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nanocursor.agents.task_manager import TaskManager
from nanocursor.commands.handlers.tasks import create_tasks_handler
from nanocursor.commands.registry import CommandContext


@pytest.mark.asyncio
async def test_tasks_cancellation_stays_stopping_through_async_cleanup():
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def run(*_args):
        started.set()
        await asyncio.Event().wait()
    async def cleanup():
        cleaning.set()
        await release.wait()
        return "\nWorktree retained"
    agent = SimpleNamespace(run_to_completion=run, worktree_cleanup=cleanup,
                            total_input_tokens=0, total_output_tokens=0, team_name="")
    tasks, messages = TaskManager(), []
    task_id = tasks.launch(agent, "task")
    handler = create_tasks_handler(tasks)
    ctx = CommandContext("", None, None, None, None, None,
                         SimpleNamespace(add_system_message=messages.append), {})
    await started.wait()
    try:
        ctx.args = f"cancel {task_id}"
        await handler(ctx)
        assert "已请求停止" in messages[-1] and "已取消" not in messages[-1]
        await cleaning.wait()
        assert tasks.has_active_tasks()
        # Business status is final; the command must still report live cleanup.
        assert tasks.get(task_id).status == "cancelled"
        for args in ("", f"info {task_id}"):
            ctx.args = args
            await handler(ctx)
            status_text = messages[-1].split("  结果:", 1)[0]
            assert "stopping" in status_text and "cancelled" not in status_text
    finally:
        release.set()
        assert await tasks.cancel_and_wait(task_id)
    ctx.args = f"info {task_id}"
    await handler(ctx)
    assert "cancelled" in messages[-1] and "stopping" not in messages[-1]
    assert "Worktree retained" in messages[-1]
