from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import partial

from nanocursor.hooks.executors import execute_action
from nanocursor.recovery import RecoveryError
from nanocursor.recovery.lifecycle import prepare_effect, run_effect
from nanocursor.hooks.models import ActionResult, Hook, HookContext, ToolRejectedError

log = logging.getLogger(__name__)


@dataclass
class HookNotification:
    hook_id: str
    event: str
    output: str
    success: bool


class HookEngine:
    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks: list[Hook] = hooks or []
        self._prompt_messages: list[str] = []
        self._notifications: list[HookNotification] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False


    def find_matching_hooks(self, event: str, ctx: HookContext) -> list[Hook]:
        matched: list[Hook] = []
        for hook in self.hooks:
            if hook.event != event:
                continue
            if not hook.should_run():
                continue
            if hook.condition is not None and not hook.condition.evaluate(ctx):
                continue
            matched.append(hook)
        return matched


    async def run_hooks(self, event: str, ctx: HookContext) -> None:
        if self._closing:
            return
        matched = self.find_matching_hooks(event, ctx)
        for hook in matched:
            prepared = self._prepare(hook, ctx)
            hook.mark_executed()
            if hook.async_exec:
                started = [False]
                task = asyncio.create_task(self._run_background(hook, ctx, prepared, started))
                self._tasks.add(task)
                task.add_done_callback(partial(self._task_done, prepared=prepared, started=started))
            else:
                await self._run_single(hook, ctx, prepared)

    async def shutdown(self, timeout: float = 5.0) -> bool:
        self._closing = True
        tasks = {task for task in self._tasks if not task.done()}
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        return not pending


    async def _run_background(self, hook, ctx, prepared, started) -> None:
        started[0] = True
        await self._run_single(hook, ctx, prepared)

    def _task_done(self, task: asyncio.Task, *, prepared=None, started=None) -> None:
        self._tasks.discard(task)
        if task.cancelled() and started is not None and not started[0]:
            runtime, operation_id = prepared
            if runtime and not runtime.store.failed:
                try:
                    runtime.not_started(operation_id, "Background hook cancelled before its coroutine started")
                except RecoveryError:
                    log.exception("Could not record hook cancellation")
        if not task.cancelled() and task.exception() is not None:
            log.error("Background hook stopped: %s", task.exception())

    @staticmethod
    def _prepare(hook, ctx):
        if hook.action.type not in {"command", "http"}:
            return (None, None)
        # Configured hooks may contain credentials. Record the identity and
        # action type, never HTTP authorization headers or expanded secrets.
        return prepare_effect("hook", hook.id, {"event": ctx.event_name, "type": hook.action.type})

    async def _run_single(self, hook: Hook, ctx: HookContext, prepared=None) -> None:
        try:
            result = await run_effect("hook", hook.id, lambda: execute_action(hook.action, ctx),
                                      prepared=prepared if prepared is not None else self._prepare(hook, ctx))
            if hook.action.type == "prompt" and result.success:
                self._prompt_messages.append(result.output)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event=hook.event,
                    output=result.output,
                    success=result.success,
                )
            )
            if not result.success:
                log.warning(
                    "Hook '%s' action failed: %s", hook.id, result.output
                )
        except RecoveryError:
            raise
        except Exception as e:
            log.warning("Hook '%s' execution error: %s", hook.id, e)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event=hook.event,
                    output=str(e),
                    success=False,
                )
            )


    async def run_pre_tool_hooks(
        self, ctx: HookContext
    ) -> ToolRejectedError | None:
        matched = self.find_matching_hooks("pre_tool_use", ctx)
        for hook in matched:
            hook.mark_executed()
            try:
                result = await run_effect("hook", hook.id, lambda: execute_action(hook.action, ctx),
                                          prepared=self._prepare(hook, ctx))
                self._notifications.append(
                    HookNotification(
                        hook_id=hook.id,
                        event="pre_tool_use",
                        output=result.output,
                        success=result.success,
                    )
                )
                if hook.reject:
                    return ToolRejectedError(
                        tool=ctx.tool_name,
                        reason=result.output,
                        hook_id=hook.id,
                    )
            except RecoveryError:
                raise
            except Exception as e:
                log.warning("Hook '%s' execution error: %s", hook.id, e)
                if hook.reject:
                    return ToolRejectedError(ctx.tool_name, f"Security hook failed: {e}", hook.id)
        return None

    def get_prompt_messages(self) -> list[str]:
        messages = list(self._prompt_messages)
        self._prompt_messages.clear()
        return messages


    def drain_notifications(self) -> list[HookNotification]:
        notifications = list(self._notifications)
        self._notifications.clear()
        return notifications
