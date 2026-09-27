from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from nanocursor.agent import Agent

log = logging.getLogger(__name__)


@dataclass
class ProgressInfo:
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_activity: str = ""


@dataclass
class BackgroundTask:
    id: str
    name: str
    agent: Agent
    task: str
    session_id: str = ""
    status: str = "running"
    result: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    cancel: Callable[[], None] | None = None
    progress: ProgressInfo = field(default_factory=ProgressInfo)


class TaskManager:


    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()
        self._async_tasks: dict[str, asyncio.Task[None]] = {}
        self.current_session_id = ""
        self._closing = False


    def launch(
        self,
        agent: Agent,
        task: str,
        name: str = "",
        fork_conversation: Any = None,
        session_id: str | None = None,
    ) -> str:
        if self._closing:
            raise RuntimeError("Task manager is shutting down")
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task,
            session_id=self.current_session_id if session_id is None else session_id,
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(
            self._run_background(task_id, fork_conversation)
        )
        self._async_tasks[task_id] = async_task

        bg.cancel = lambda: self.cancel(task_id)
        async_task.add_done_callback(lambda task: self._task_done(task_id, task))
        return task_id


    async def _run_background(
        self, task_id: str, fork_conversation: Any = None
    ) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            if fork_conversation is not None:
                result = await bg.agent.run_to_completion("", fork_conversation)
            else:
                result = await bg.agent.run_to_completion(bg.task)
            bg.result = result
            bg.status = "completed"

            if bg.agent.team_name and bg.agent._team_manager:
                mailbox = bg.agent._team_manager.get_mailbox(bg.agent.team_name)
                if mailbox:
                    bg.status = "idle"
                    from nanocursor.teams.mailbox import create_message
                    msg = create_message(
                        from_agent=bg.name,
                        to_agent="lead",
                        content=f"[idle] {bg.name}: completed initial task",
                        summary=f"{bg.name} idle",
                    )
                    mailbox.write("lead", msg)

                    for _ in range(60):
                        await asyncio.sleep(1)
                        msgs = mailbox.consume(bg.agent.agent_id)
                        if not msgs:
                            continue
                        prompt = "\n\n".join(
                            f"[Message from {m.from_agent}] {m.content}" for m in msgs
                        )
                        bg.status = "running"
                        result = await bg.agent.run_to_completion(prompt)
                        bg.result = result
                        bg.status = "idle"
                        msg = create_message(
                            from_agent=bg.name,
                            to_agent="lead",
                            content=f"[idle] {bg.name}: completed follow-up",
                            summary=f"{bg.name} idle",
                        )
                        mailbox.write("lead", msg)
                    bg.status = "completed"

        except asyncio.CancelledError:
            bg.status = "cancelled"
            bg.result = (bg.result + "\nTask was cancelled").strip()
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            cleanup = getattr(bg.agent, "worktree_cleanup", None)
            if cleanup is not None:
                try:
                    bg.result += await cleanup()
                except Exception as exc:
                    log.exception("Worktree cleanup failed")
                    bg.result += f"\nWorktree cleanup failed: {exc}"


    def adopt_running(
        self,
        agent: Agent,
        task_description: str,
        partial_result: str = "",
        name: str = "",
        session_id: str | None = None,
    ) -> str:
        if self._closing:
            raise RuntimeError("Task manager is shutting down")
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task_description,
            result=partial_result,
            session_id=self.current_session_id if session_id is None else session_id,
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(self._continue_background(task_id))
        self._async_tasks[task_id] = async_task
        bg.cancel = lambda: self.cancel(task_id)
        async_task.add_done_callback(lambda task: self._task_done(task_id, task))
        return task_id


    async def _continue_background(self, task_id: str) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            result = await bg.agent.run_to_completion(bg.task)
            bg.result = (bg.result + "\n" + result).strip() if bg.result else result
            bg.status = "completed"
        except asyncio.CancelledError:
            bg.status = "cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"
        finally:
            cleanup = getattr(bg.agent, "worktree_cleanup", None)
            if cleanup is not None:
                try:
                    bg.result += await cleanup()
                except Exception as exc:
                    log.exception("Worktree cleanup failed")
                    bg.result += f"\nWorktree cleanup failed: {exc}"

    def _task_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        bg = self._tasks[task_id]
        if task.cancelled():
            bg.status = "cancelled"
        elif task.exception() is not None:
            bg.status = "failed"
            bg.result += f"\nTask cleanup failed: {task.exception()}"
        bg.end_time = time.monotonic()
        bg.progress.input_tokens = bg.agent.total_input_tokens
        bg.progress.output_tokens = bg.agent.total_output_tokens
        self._async_tasks.pop(task_id, None)
        self._notify_queue.put_nowait(task_id)

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        bg = self._tasks.get(task_id)
        if bg is None:
            return False
        async_task = self._async_tasks.get(task_id)
        if async_task and not async_task.done():
            # Cancelling an already stopping task can interrupt its finally block.
            if not async_task.cancelling():
                bg.status = "stopping"
                async_task.cancel()
            return True
        return False

    def active_tasks(
        self, session_id: str | None = None, team_name: str | None = None,
    ) -> list[BackgroundTask]:
        return [bg for task_id, bg in self._tasks.items()
                if task_id in self._async_tasks and not self._async_tasks[task_id].done()
                and (session_id is None or bg.session_id == session_id)
                and (team_name is None or getattr(bg.agent, "team_name", "") == team_name)]

    def has_active_tasks(
        self, session_id: str | None = None, team_name: str | None = None,
    ) -> bool:
        return bool(self.active_tasks(session_id, team_name))

    async def cancel_and_wait(self, task_id: str, timeout: float = 5.0) -> bool:
        task = self._async_tasks.get(task_id)
        if task is None or task.done():
            return True
        self.cancel(task_id)
        _, pending = await asyncio.wait({task}, timeout=timeout)
        return not pending

    async def shutdown(
        self, session_id: str | None = None, team_name: str | None = None,
        timeout: float = 5.0,
    ) -> bool:
        if session_id is None and team_name is None:
            self._closing = True
        tasks = self.active_tasks(session_id, team_name)
        handles = {self._async_tasks[bg.id] for bg in tasks}
        for bg in tasks:
            self.cancel(bg.id)
        if not handles:
            return True
        _, pending = await asyncio.wait(handles, timeout=timeout)
        return not pending

    def poll_completed(self, session_id: str | None = None) -> list[BackgroundTask]:
        completed: list[BackgroundTask] = []
        for _ in range(self._notify_queue.qsize()):
            try:
                task_id = self._notify_queue.get_nowait()
                bg = self._tasks.get(task_id)
                if bg is not None:
                    if session_id is None or bg.session_id == session_id:
                        completed.append(bg)
                    else:
                        self._notify_queue.put_nowait(task_id)
            except asyncio.QueueEmpty:
                break
        return completed
