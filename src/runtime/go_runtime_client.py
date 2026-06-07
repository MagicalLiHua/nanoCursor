"""
Compatibility shim -- prefer src.runtime.executor_client for new code.
This module delegates to executor_client when available, falls back to HTTP.
"""

from __future__ import annotations

import json
import asyncio
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from src.runtime.runtime_feature_flags import go_runtime_timeout_ms, go_runtime_url


class GoRuntimeUnavailable(RuntimeError):
    """Raised when the Go runtime cannot be reached."""


class GoRuntimeError(RuntimeError):
    """Raised when the Go runtime returns an invalid response."""


TERMINAL_STATUSES = {"completed", "failed", "denied", "timeout", "cancelled"}
RuntimeEventCallback = Callable[[dict[str, Any]], None]


def normalize_command_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Go tool run result to the Python command runner schema."""
    return {
        "backend": str(payload.get("backend") or "go_runtime"),
        "command": str(payload.get("command") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "exit_code": int(payload.get("exit_code") if payload.get("exit_code") is not None else -1),
        "stdout": str(payload.get("stdout") or ""),
        "stderr": str(payload.get("stderr") or payload.get("message") or ""),
        "stdout_truncated": bool(payload.get("stdout_truncated")),
        "stderr_truncated": bool(payload.get("stderr_truncated")),
        "duration_ms": int(payload.get("duration_ms") or 0),
        "timed_out": bool(payload.get("timed_out") or payload.get("status") == "timeout"),
        "tool_run_id": payload.get("tool_run_id"),
        "status": payload.get("status"),
        "error_code": payload.get("error_code"),
        "evidence": payload.get("evidence") if isinstance(payload.get("evidence"), dict) else None,
        "runtime_events": payload.get("runtime_events") if isinstance(payload.get("runtime_events"), list) else [],
    }


def run_command_via_go_runtime(
    command: str,
    cwd: str | Path,
    timeout_seconds: int | float = 120,
    max_stdout_chars: int = 100_000,
    max_stderr_chars: int = 20_000,
    permission_level: str = "shell_safe",
    approval_id: str | None = None,
    approval_token: str | None = None,
    run_id: str | None = None,
    on_runtime_event: RuntimeEventCallback | None = None,
    event_poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    cwd = str(Path(cwd).resolve())
    payload = {
        "workspace_dir": cwd,
        "run_id": run_id,
        "tool": "run_command",
        "input": {
            "command": command,
            "cwd": cwd,
            "timeout_ms": int(float(timeout_seconds) * 1000),
            "max_stdout_chars": max_stdout_chars,
            "max_stderr_chars": max_stderr_chars,
        },
        "policy": {
            "permission_level": permission_level,
            "requires_approval": permission_level in {"shell_risky", "risky_write", "external_risky", "mcp_write"},
            "approval_id": approval_id,
            "approval_token": approval_token,
        },
    }
    started = _post_json("/v1/tools/execute", payload)
    tool_run_id = started.get("tool_run_id")
    if not tool_run_id:
        raise GoRuntimeError(f"Go runtime did not return tool_run_id: {started!r}")
    runtime_events: list[dict[str, Any]] = []
    result = _poll_tool_run(
        str(tool_run_id),
        timeout_seconds=float(timeout_seconds) + 2.0,
        on_runtime_event=on_runtime_event,
        runtime_events=runtime_events,
        event_poll_interval_seconds=event_poll_interval_seconds,
    )
    return normalize_command_result(result)


async def run_command_via_go_runtime_async(
    command: str,
    cwd: str | Path,
    timeout_seconds: int | float = 120,
    max_stdout_chars: int = 100_000,
    max_stderr_chars: int = 20_000,
    permission_level: str = "shell_safe",
    approval_id: str | None = None,
    approval_token: str | None = None,
    run_id: str | None = None,
    on_runtime_event: RuntimeEventCallback | None = None,
    event_poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    """Async boundary for the synchronous Go runtime HTTP adapter."""
    return await asyncio.to_thread(
        run_command_via_go_runtime,
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_stdout_chars=max_stdout_chars,
        max_stderr_chars=max_stderr_chars,
        permission_level=permission_level,
        approval_id=approval_id,
        approval_token=approval_token,
        run_id=run_id,
        on_runtime_event=on_runtime_event,
        event_poll_interval_seconds=event_poll_interval_seconds,
    )


def health() -> dict[str, Any]:
    return _get_json("/health")


def _poll_tool_run(
    tool_run_id: str,
    timeout_seconds: float,
    *,
    on_runtime_event: RuntimeEventCallback | None = None,
    runtime_events: list[dict[str, Any]] | None = None,
    event_poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(timeout_seconds, 1.0)
    cursor = 0
    events = runtime_events if runtime_events is not None else []
    poll_interval = max(0.02, min(float(event_poll_interval_seconds or 0.1), 1.0))
    while True:
        cursor = _collect_tool_run_events(
            tool_run_id,
            cursor=cursor,
            sink=events,
            on_runtime_event=on_runtime_event,
        )
        payload = _get_json(f"/v1/tools/runs/{tool_run_id}")
        if payload.get("status") in TERMINAL_STATUSES:
            cursor = _collect_tool_run_events(
                tool_run_id,
                cursor=cursor,
                sink=events,
                on_runtime_event=on_runtime_event,
            )
            payload["runtime_events"] = events
            return payload
        if time.monotonic() >= deadline:
            raise GoRuntimeUnavailable(f"Go runtime tool run did not finish: {tool_run_id}")
        time.sleep(poll_interval)


def _tool_run_events(tool_run_id: str) -> list[dict[str, Any]]:
    payload = _get_json(f"/v1/tools/runs/{tool_run_id}/events")
    events = payload.get("events") if isinstance(payload, dict) else []
    return events if isinstance(events, list) else []


def _collect_tool_run_events(
    tool_run_id: str,
    *,
    cursor: int,
    sink: list[dict[str, Any]],
    on_runtime_event: RuntimeEventCallback | None = None,
) -> int:
    payload = _get_json(f"/v1/tools/runs/{tool_run_id}/events?after={max(cursor, 0)}")
    raw_events = payload.get("events") if isinstance(payload, dict) else []
    events = raw_events if isinstance(raw_events, list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        sink.append(event)
        if on_runtime_event is not None:
            on_runtime_event(event)
    next_cursor = payload.get("cursor") if isinstance(payload, dict) else None
    if isinstance(next_cursor, int):
        return max(next_cursor, cursor + len(events))
    return cursor + len(events)


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _request_json("POST", path, payload)


def _get_json(path: str) -> dict[str, Any]:
    return _request_json("GET", path, None)


def _request_json(method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    url = f"{go_runtime_url()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    timeout = go_runtime_timeout_ms() / 1000
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as json_exc:
            raise GoRuntimeError(f"Go runtime HTTP {exc.code}: {raw}") from json_exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GoRuntimeUnavailable(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise GoRuntimeError(f"Go runtime returned invalid JSON from {url}") from exc
