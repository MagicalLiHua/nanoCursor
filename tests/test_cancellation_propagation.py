"""Tests for cancellation propagation from Lead to sub-agents."""

import asyncio
import json
import pytest

from src.agent.agent_pool import AgentPool, get_or_create_pool, get_pool, cleanup_pool
from src.agent.engine import bind_runtime_context, handle_spawn_agent, handle_gather_agents
from src.api.services.event_store import EventStore


def test_cancel_all_stops_running_agents():
    """cancel_all() should cancel all running agent tasks."""
    async def run():
        pool = AgentPool(max_concurrent=3)
        started = []
        cancelled = []

        async def slow_runner(prompt, **kwargs):
            started.append(prompt)
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.append(prompt)
                raise
            return "should not reach"

        h1 = await pool.submit("A", "a", "task-1", slow_runner)
        h2 = await pool.submit("B", "b", "task-2", slow_runner)
        await asyncio.sleep(0.05)  # let tasks start

        pool.cancel_all()

        # Wait for tasks to finish cancellation
        await asyncio.sleep(0.1)

        assert h1.status == "cancelled"
        assert h2.status == "cancelled"
        assert len(cancelled) == 2

    asyncio.run(run())


def test_cancel_propagation_via_should_cancel():
    """When _should_cancel_run detects cancellation, it should cancel the pool."""
    from api_server import _cancel_agent_pool

    thread_id = "test-cancel-prop"

    # Create a pool with a slow agent
    async def run():
        pool = get_or_create_pool(thread_id)
        cancelled = []

        async def slow_runner(prompt, **kwargs):
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.append(prompt)
                raise

        handle = await pool.submit("A", "a", "task-1", slow_runner)
        await asyncio.sleep(0.05)

        # Simulate cancellation by calling _cancel_agent_pool directly
        _cancel_agent_pool(thread_id)

        await asyncio.sleep(0.1)
        assert handle.status == "cancelled"
        assert len(cancelled) == 1

        cleanup_pool(thread_id)

    asyncio.run(run())


def test_pool_status_callback_receives_events():
    """Status callback should be called on agent lifecycle events."""
    async def run():
        pool = AgentPool(max_concurrent=3)
        events = []

        def on_status(handle, event):
            events.append({
                "agent_id": handle.agent_id,
                "name": handle.name,
                "event": event,
                "status": handle.status,
            })

        pool.set_status_callback(on_status)

        async def fake_runner(prompt, **kwargs):
            return "done"

        h = await pool.submit("TestAgent", "tester", "test-goal", fake_runner)
        await pool.gather()

        assert len(events) == 2  # started + completed
        assert events[0]["event"] == "started"
        assert events[0]["status"] == "running"
        assert events[1]["event"] == "completed"
        assert events[1]["status"] == "completed"

    asyncio.run(run())


def test_pool_status_callback_on_failure():
    """Status callback should receive 'failed' event when agent fails."""
    async def run():
        pool = AgentPool(max_concurrent=3)
        events = []

        def on_status(handle, event):
            events.append({"event": event, "status": handle.status, "error": handle.error})

        pool.set_status_callback(on_status)

        async def failing_runner(prompt, **kwargs):
            raise RuntimeError("agent crashed")

        h = await pool.submit("FailAgent", "tester", "fail-goal", failing_runner)
        await pool.gather()

        assert len(events) == 2  # started + failed
        assert events[0]["event"] == "started"
        assert events[1]["event"] == "failed"
        assert events[1]["status"] == "failed"
        assert "agent crashed" in events[1]["error"]

    asyncio.run(run())


def test_pool_status_callback_on_cancel():
    """Status callback should receive 'cancelled' event when pool is cancelled."""
    async def run():
        pool = AgentPool(max_concurrent=3)
        events = []

        def on_status(handle, event):
            events.append({"event": event, "name": handle.name})

        pool.set_status_callback(on_status)

        async def slow_runner(prompt, **kwargs):
            await asyncio.sleep(100)

        await pool.submit("SlowAgent", "worker", "slow-goal", slow_runner)
        await asyncio.sleep(0.05)

        pool.cancel_all()
        await asyncio.sleep(0.1)

        assert any(e["event"] == "cancelled" for e in events)

    asyncio.run(run())


def test_pool_callback_survives_exception():
    """Pool should not crash if callback raises an exception."""
    async def run():
        pool = AgentPool(max_concurrent=3)

        def bad_callback(handle, event):
            raise RuntimeError("callback error")

        pool.set_status_callback(bad_callback)

        async def fake_runner(prompt, **kwargs):
            return "done"

        # Should not raise despite bad callback
        h = await pool.submit("A", "a", "goal", fake_runner)
        await pool.gather()
        assert h.status == "completed"

    asyncio.run(run())
