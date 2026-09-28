from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from nanocursor.conversation import Message
from nanocursor.recovery.lifecycle import prepare_effect, run_effect
from nanocursor.recovery.runtime import RecoveryRuntime, current_runtime
from nanocursor.recovery.store import now

if TYPE_CHECKING:
    from nanocursor.agent import Agent

log = logging.getLogger(__name__)


def task_runtime(agent):
    runtime = current_runtime()
    inherited = getattr(agent, "recovery", None)
    return runtime or (inherited if isinstance(inherited, RecoveryRuntime) else None)


def unsettled_descendants(runtime, operation_id: str) -> bool:
    """A stopped local coroutine need not imply that its child effect stopped."""
    if runtime is None:
        return False
    return bool(runtime.store.rows(
        "WITH RECURSIVE children(operation_id,state) AS ("
        " SELECT operation_id,state FROM operations WHERE parent_operation_id=?"
        " UNION ALL SELECT o.operation_id,o.state FROM operations o JOIN children c ON o.parent_operation_id=c.operation_id"
        ") SELECT operation_id FROM children WHERE state IN ('intent','outcome_unknown') LIMIT 1",
        (operation_id,),
    ))


@dataclass(frozen=True)
class NotificationTarget:
    runtime: Any
    session: Any
    session_key: str
    generation: int
    run_id: str | None

    @classmethod
    def capture(cls, runtime):
        if runtime is None:
            return None
        host = runtime if runtime.session is not None else runtime._tree_root
        if host.session is None or not host.session_key:
            return None
        return cls(host, host.session, host.session_key, host.generation, host.run_id)


def notification_payload(target: NotificationTarget | None, operation_id: str | None, content: str, kind: str):
    if target is None or operation_id is None:
        return None
    return {"session_key": target.session_key, "record": {
        "type": "user", "content": content, "timestamp": now(),
        "record_id": "notification_" + operation_id, "generation": target.generation,
        "run_id": target.run_id,
        "recovery_source": {"operation_id": operation_id, "kind": kind},
    }}


def publish_notification(target: NotificationTarget | None, payload):
    """Return a durable pending notification without interrupting a tool batch."""
    if target is None or payload is None:
        return None
    runtime = target.runtime
    rows = runtime.store.rows("SELECT generation FROM sessions WHERE session_key=?", (target.session_key,))
    if not rows or rows[0]["generation"] != target.generation:
        # The complete payload remains in lifecycle evidence for the old branch.
        return None
    record = payload["record"]
    message = Message(role="user", content=record["content"])
    message._durable_notification = payload
    return message


def persist_notification_message(session, message: Message) -> bool:
    """Called by the host while flushing notifications between model batches."""
    payload = getattr(message, "_durable_notification", None)
    if payload is None:
        return False
    from nanocursor.memory.session import SessionRecord
    from nanocursor.recovery import RecoveryIntegrityError

    runtime = session._recovery
    if runtime is None or runtime.session_key != payload["session_key"]:
        raise RecoveryIntegrityError("Notification belongs to a different session")
    record = payload["record"]
    if runtime.generation != record["generation"]:
        raise RecoveryIntegrityError("Notification belongs to an earlier conversation branch")
    ids = getattr(message, "_recovery_record_ids", {})
    if payload["session_key"] in ids:
        return True
    session.append_record(SessionRecord.from_jsonl(json.dumps(record)))
    ids[payload["session_key"]] = [record["record_id"]]
    message._recovery_record_ids = ids
    return True


@dataclass
class LifecycleResult:
    output: dict
    success: bool = True
    outcome_unknown: bool = False


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
    operation_id: str | None = None
    notification_message: Message | None = None
    recovery_error: str = ""
    _prepared: Any = field(default=None, repr=False)
    _notification_target: NotificationTarget | None = field(default=None, repr=False)
    _notification_payload: Any = field(default=None, repr=False)
    _execution_started: bool = field(default=False, repr=False)


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
        task_id = uuid.uuid4().hex
        runtime = task_runtime(agent)
        with runtime.activate() if runtime else nullcontext():
            prepared = prepare_effect("background", name or task_id, {
                "task_id": task_id, "prompt": task, "cwd": str(getattr(agent, "work_dir", "")),
            })
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task,
            session_id=self.current_session_id if session_id is None else session_id,
            operation_id=prepared[1], _prepared=prepared,
            _notification_target=NotificationTarget.capture(runtime),
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(
            self._tracked_background(task_id, fork_conversation)
        )
        self._async_tasks[task_id] = async_task

        bg.cancel = lambda: self.cancel(task_id)
        async_task.add_done_callback(lambda task: self._task_done(task_id, task))
        return task_id

    async def _tracked_background(self, task_id: str, fork_conversation=None, *, adopted=False) -> None:
        bg = self._tasks[task_id]
        bg._execution_started = True

        async def execute():
            if adopted:
                await self._continue_background(task_id)
            else:
                await self._run_background(task_id, fork_conversation)
            bg.end_time = time.monotonic()
            bg.progress.input_tokens = bg.agent.total_input_tokens
            bg.progress.output_tokens = bg.agent.total_output_tokens
            from nanocursor.agents.notification import format_task_notification
            content = format_task_notification(bg) + "\nHistorical task output; not new user authorization."
            bg._notification_payload = notification_payload(bg._notification_target, bg.operation_id, content, "background")
            output = {"task_id": bg.id, "name": bg.name, "status": bg.status, "result": bg.result,
                      "notification": bg._notification_payload}
            runtime = bg._prepared[0]
            if runtime:
                runtime.store.put_metadata("background_result", bg.operation_id, output)
            return LifecycleResult(output, bg.status == "completed", unsettled_descendants(runtime, bg.operation_id))

        await run_effect("background", bg.name, execute, prepared=bg._prepared)
        bg.notification_message = publish_notification(bg._notification_target, bg._notification_payload)


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
            self._observe_result(bg)

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
                        self._observe_result(bg)
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
            bg.result = (bg.result + f"\nError: {e}").strip()
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
        runtime = task_runtime(agent)
        if runtime:
            raise RuntimeError(
                "Cannot adopt an existing execution by rerunning its description. "
                "Keep its original task handle or explicitly launch a new task after review."
            )
        task_id = uuid.uuid4().hex
        with runtime.activate() if runtime else nullcontext():
            prepared = prepare_effect("background", name or task_id, {
                "task_id": task_id, "prompt": task_description, "adopted": True,
                "cwd": str(getattr(agent, "work_dir", "")),
            })
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task_description,
            result=partial_result,
            session_id=self.current_session_id if session_id is None else session_id,
            operation_id=prepared[1], _prepared=prepared,
            _notification_target=NotificationTarget.capture(runtime),
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(self._tracked_background(task_id, adopted=True))
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
            self._observe_result(bg)
        except asyncio.CancelledError:
            bg.status = "cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = (bg.result + f"\nError: {e}").strip()
        finally:
            cleanup = getattr(bg.agent, "worktree_cleanup", None)
            if cleanup is not None:
                try:
                    bg.result += await cleanup()
                except Exception as exc:
                    log.exception("Worktree cleanup failed")
                    bg.result += f"\nWorktree cleanup failed: {exc}"

    @staticmethod
    def _observe_result(bg: BackgroundTask) -> None:
        """Keep observed output even if later idle waiting or cleanup is interrupted."""
        runtime = bg._prepared[0] if bg._prepared else None
        if runtime:
            runtime.store.put_metadata("background_observation", bg.operation_id, {
                "task_id": bg.id, "name": bg.name, "status": bg.status,
                "result": bg.result, "observed_at": now(),
            })

    def _task_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        bg = self._tasks[task_id]
        runtime = bg._prepared[0] if bg._prepared else None
        if task.cancelled():
            bg.status = "cancelled"
        elif task.exception() is not None:
            bg.recovery_error = str(task.exception())
            if not bg._notification_payload:
                bg.status = "failed"
                bg.result += f"\nTask cleanup failed: {task.exception()}"
        try:
            if runtime and not bg._execution_started:
                runtime.not_started(bg.operation_id, "Cancelled before the background coroutine started")
            elif runtime and (task.cancelled() or task.exception() is not None):
                runtime.mark_unknown(bg.operation_id, "Background lifecycle interrupted: " + (bg.recovery_error or bg.status))
        except Exception as exc:
            bg.recovery_error = str(exc)
            log.error("Cannot persist background terminal state: %s", exc)
        bg.end_time = bg.end_time or time.monotonic()
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
                runtime = bg._prepared[0] if bg._prepared else None
                if runtime:
                    runtime.store.put_metadata("background_cancel", bg.operation_id, {"requested_at": now(), "task_id": bg.id})
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
