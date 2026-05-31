"""Agent execution pool for concurrent subagent management.

Allows the Lead agent to spawn multiple subagents that run concurrently
as asyncio.Tasks, with semaphore-based concurrency limiting.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AgentHandle:
    """Handle for a concurrently running agent."""
    agent_id: str
    name: str
    role: str
    goal: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    task: asyncio.Task | None = None
    result: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    tool_outputs: list[str] = field(default_factory=list)


class AgentPool:
    """Manages concurrent agents within a single run."""

    def __init__(self, max_concurrent: int = 3):
        self._agents: dict[str, AgentHandle] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._status_callback: Callable[[AgentHandle, str], None] | None = None

    def set_status_callback(self, callback: Callable[[AgentHandle, str], None]):
        """Set a callback for agent status changes."""
        self._status_callback = callback

    async def submit(
        self,
        name: str,
        role: str,
        goal: str,
        runner: Callable[..., Any],
        tools: list | None = None,
        **runner_kwargs,
    ) -> AgentHandle:
        """Submit an agent for concurrent execution. Returns handle immediately."""
        handle = AgentHandle(
            agent_id=str(uuid.uuid4()),
            name=name,
            role=role,
            goal=goal,
            status="pending",
        )
        self._agents[handle.agent_id] = handle
        handle.task = asyncio.create_task(
            self._run_agent(handle, runner, tools, **runner_kwargs)
        )
        return handle

    async def gather(self, agent_ids: list[str] | None = None) -> dict[str, AgentHandle]:
        """Wait for specified agents (or all) to complete. Returns handles."""
        if agent_ids is None:
            targets = [h for h in self._agents.values() if h.task and not h.task.done()]
        else:
            targets = [self._agents[aid] for aid in agent_ids if aid in self._agents and self._agents[aid].task and not self._agents[aid].task.done()]

        if targets:
            await asyncio.gather(*[h.task for h in targets], return_exceptions=True)

        if agent_ids is None:
            return dict(self._agents)
        return {aid: self._agents[aid] for aid in agent_ids if aid in self._agents}

    def cancel_all(self):
        """Cancel all running agents."""
        for handle in self._agents.values():
            if handle.task and not handle.task.done():
                handle.task.cancel()
                handle.status = "cancelled"
                handle.completed_at = time.time()
                self._notify(handle, "cancelled")

    def get_agent(self, agent_id: str) -> AgentHandle | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentHandle]:
        return list(self._agents.values())

    def active_count(self) -> int:
        return sum(1 for h in self._agents.values() if h.status in ("pending", "running"))

    def _notify(self, handle: AgentHandle, event: str):
        if self._status_callback:
            try:
                self._status_callback(handle, event)
            except Exception:
                pass

    async def _run_agent(
        self,
        handle: AgentHandle,
        runner: Callable[..., Any],
        tools: list | None = None,
        **runner_kwargs,
    ):
        """Run agent with semaphore limiting."""
        async with self._semaphore:
            handle.status = "running"
            self._notify(handle, "started")
            try:
                result = await runner(
                    prompt=handle.goal,
                    tools=tools,
                    **runner_kwargs,
                )
                handle.result = result if isinstance(result, str) else str(result)
                handle.status = "completed"
                handle.completed_at = time.time()
                self._notify(handle, "completed")
            except asyncio.CancelledError:
                handle.status = "cancelled"
                handle.completed_at = time.time()
                self._notify(handle, "cancelled")
                raise
            except Exception as e:
                handle.error = str(e)
                handle.status = "failed"
                handle.completed_at = time.time()
                self._notify(handle, "failed")


# ========== Per-thread pool registry ==========

_pools: dict[str, AgentPool] = {}


def get_or_create_pool(thread_id: str, max_concurrent: int = 3) -> AgentPool:
    """Get or create the AgentPool for a run."""
    if thread_id not in _pools:
        _pools[thread_id] = AgentPool(max_concurrent=max_concurrent)
    return _pools[thread_id]


def get_pool(thread_id: str) -> AgentPool | None:
    """Get existing AgentPool for a run."""
    return _pools.get(thread_id)


def cleanup_pool(thread_id: str):
    """Remove pool for a completed run."""
    pool = _pools.pop(thread_id, None)
    if pool:
        pool.cancel_all()


__all__ = [
    "AgentHandle", "AgentPool",
    "get_or_create_pool", "get_pool", "cleanup_pool",
]
