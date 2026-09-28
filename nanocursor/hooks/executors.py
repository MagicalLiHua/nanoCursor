from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.request import Request, urlopen
from urllib.error import URLError

from nanocursor.hooks.models import Action, ActionResult, HookContext
from nanocursor.tools.bash import _spawn_owned_shell, _terminate_process_group, _record_process
from nanocursor.recovery import RecoveryError
from nanocursor.tools.runtime import current_runtime

log = logging.getLogger(__name__)


async def execute_command(action: Action, ctx: HookContext) -> ActionResult:
    # The configured command is code; context is only data. Never interpolate it.
    command = action.command
    env = dict(os.environ)
    env.update({
        "NANOCURSOR_HOOK_EVENT": ctx.event_name,
        "NANOCURSOR_HOOK_TOOL_NAME": ctx.tool_name,
        "NANOCURSOR_HOOK_FILE_PATH": ctx.file_path,
    })
    payload = None
    if action.input == "context-json":
        payload = json.dumps({
            "schema_version": 1,
            "event": ctx.event_name,
            "tool_name": ctx.tool_name,
            "tool_args": ctx.tool_args,
            "file_path": ctx.file_path,
            "message": ctx.message,
            "error": ctx.error,
        }, ensure_ascii=False).encode("utf-8")
    context = current_runtime()
    proc = None
    try:
        proc = await _spawn_owned_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE if payload is not None else None,
            env=env,
            cwd=str(context.cwd) if context else None,
            start_new_session=os.name == "posix",
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(payload), timeout=action.timeout
            )
        except asyncio.TimeoutError:
            await _terminate_process_group(proc)
            return ActionResult(
                output=f"Command timed out after {action.timeout}s: {command}",
                success=False,
            )
        _record_process(proc)
        output = stdout.decode(errors="replace").strip() if stdout else ""
        return ActionResult(output=output, success=proc.returncode == 0)
    except asyncio.CancelledError:
        if proc is not None:
            await _terminate_process_group(proc)
        raise
    except RecoveryError:
        if proc is not None:
            await _terminate_process_group(proc)
        raise
    except Exception as e:
        if proc is not None:
            await _terminate_process_group(proc)
        return ActionResult(output=f"Command execution error: {e}", success=False,
                            outcome_unknown=proc is not None)


async def execute_prompt(action: Action, ctx: HookContext) -> ActionResult:
    message = ctx.expand(action.message)
    return ActionResult(output=message, success=True)


async def execute_http(action: Action, ctx: HookContext) -> ActionResult:
    url = ctx.expand(action.url)
    body = ctx.expand(action.body) if action.body else None
    method = action.method or "POST"

    headers = dict(action.headers)
    for k, v in headers.items():
        headers[k] = ctx.expand(v)
    if body and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"


    def _do_request() -> ActionResult:
        try:
            data = body.encode() if body else None
            req = Request(url, data=data, headers=headers, method=method)
            with urlopen(req, timeout=30) as resp:
                resp_body = resp.read().decode(errors="replace")[:500]
                return ActionResult(
                    output=f"HTTP {resp.status}: {resp_body}",
                    success=200 <= resp.status < 300,
                )
        except URLError as e:
            return ActionResult(output=f"HTTP error: {e}", success=False, outcome_unknown=True)
        except Exception as e:
            return ActionResult(output=f"HTTP error: {e}", success=False, outcome_unknown=True)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_request)


async def execute_agent(action: Action, ctx: HookContext) -> ActionResult:
    return ActionResult(
        output="agent Hook is not supported",
        success=False,
    )


_EXECUTOR_MAP = {
    "command": execute_command,
    "prompt": execute_prompt,
    "http": execute_http,
    "agent": execute_agent,
}


async def execute_action(action: Action, ctx: HookContext) -> ActionResult:
    executor = _EXECUTOR_MAP.get(action.type)
    if executor is None:
        return ActionResult(
            output=f"Unknown action type: {action.type}",
            success=False,
        )
    return await executor(action, ctx)
