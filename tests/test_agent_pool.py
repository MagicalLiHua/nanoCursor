"""Tests for AgentPool and gather_agents."""

import asyncio
import json
import pytest

from src.agent.agent_pool import AgentPool, AgentHandle, get_or_create_pool, get_pool, cleanup_pool


def test_agent_pool_submit_and_gather():
    async def run():
        pool = AgentPool(max_concurrent=3)

        async def fake_runner(prompt, **kwargs):
            return f"result for {prompt}"

        h1 = await pool.submit("Coder", "coder", "implement feature A", fake_runner)
        h2 = await pool.submit("Tester", "tester", "test feature A", fake_runner)

        assert h1.status in ("pending", "running")
        assert h2.status in ("pending", "running")

        results = await pool.gather()

        assert results[h1.agent_id].status == "completed"
        assert results[h1.agent_id].result == "result for implement feature A"
        assert results[h2.agent_id].status == "completed"
        assert results[h2.agent_id].result == "result for test feature A"

    asyncio.run(run())


def test_agent_pool_gather_specific_ids():
    async def run():
        pool = AgentPool(max_concurrent=3)

        async def fake_runner(prompt, **kwargs):
            return f"done: {prompt}"

        h1 = await pool.submit("A", "a", "task-1", fake_runner)
        h2 = await pool.submit("B", "b", "task-2", fake_runner)

        # Gather only h1
        results = await pool.gather([h1.agent_id])
        assert h1.agent_id in results
        assert results[h1.agent_id].status == "completed"

        # h2 should also be done since gather waits for all
        assert h2.status == "completed"

    asyncio.run(run())


def test_agent_pool_cancel_all():
    async def run():
        pool = AgentPool(max_concurrent=3)

        async def slow_runner(prompt, **kwargs):
            await asyncio.sleep(100)
            return "should not reach"

        h1 = await pool.submit("A", "a", "task-1", slow_runner)
        await asyncio.sleep(0.05)  # let task start

        pool.cancel_all()

        assert h1.status == "cancelled"

    asyncio.run(run())


def test_agent_pool_failure():
    async def run():
        pool = AgentPool(max_concurrent=3)

        async def failing_runner(prompt, **kwargs):
            raise RuntimeError("agent crashed")

        h1 = await pool.submit("A", "a", "task-1", failing_runner)
        results = await pool.gather()

        assert results[h1.agent_id].status == "failed"
        assert "agent crashed" in results[h1.agent_id].error

    asyncio.run(run())


def test_agent_pool_semaphore_limits_concurrency():
    """With max_concurrent=2, only 2 agents should run at a time."""
    async def run():
        pool = AgentPool(max_concurrent=2)
        running_count = 0
        max_running = 0

        async def tracked_runner(prompt, **kwargs):
            nonlocal running_count, max_running
            running_count += 1
            max_running = max(max_running, running_count)
            await asyncio.sleep(0.1)
            running_count -= 1
            return "done"

        handles = []
        for i in range(5):
            h = await pool.submit(f"A{i}", "a", f"task-{i}", tracked_runner)
            handles.append(h)

        await pool.gather()
        assert max_running <= 2

    asyncio.run(run())


def test_pool_registry():
    async def run():
        tid = "test-thread-pool"

        pool1 = get_or_create_pool(tid)
        pool2 = get_or_create_pool(tid)
        assert pool1 is pool2

        assert get_pool(tid) is pool1

        cleanup_pool(tid)
        assert get_pool(tid) is None

    asyncio.run(run())


def test_gather_agents_tool():
    """Test handle_gather_agents via the tool interface."""
    from src.agent.engine import handle_gather_agents, handle_spawn_agent, bind_runtime_context
    from src.api.services.event_store import EventStore
    from pathlib import Path
    import tempfile

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            workspace.mkdir()
            thread_id = "test-gather-tool"
            EventStore().create_session(thread_id, "test", str(workspace), status="running")

            async def fake_runner(prompt, **kwargs):
                return f"completed: {prompt}"

            with bind_runtime_context({
                "thread_id": thread_id,
                "workspace_dir": str(workspace),
                "agent": "Lead",
                "prompt": "test task",
                "subagent_runner": fake_runner,
            }):
                # Spawn two agents
                out1 = await handle_spawn_agent(name="A", role="a", goal="task-1", run_now=True)
                out2 = await handle_spawn_agent(name="B", role="b", goal="task-2", run_now=True)

                p1 = json.loads(out1)
                p2 = json.loads(out2)
                assert p1["ok"] and p2["ok"]

                # Gather all
                gather_result = json.loads(await handle_gather_agents())
                assert gather_result["ok"]
                assert len(gather_result["agents"]) == 2

                statuses = {a["status"] for a in gather_result["agents"]}
                assert "completed" in statuses

                cleanup_pool(thread_id)

    asyncio.run(run())
