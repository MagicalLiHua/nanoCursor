"""gRPC client for the Go executor service."""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

import grpc

from src.executor.proto import executor_pb2 as pb
from src.executor.proto import executor_pb2_grpc as pb_grpc

EXECUTOR_ADDR = os.getenv("NANOCURSOR_EXECUTOR_ADDR", "localhost:50055")
EXECUTOR_POLL_INTERVAL_SECONDS = float(os.getenv("NANOCURSOR_EXECUTOR_POLL_INTERVAL_MS", "20")) / 1000

_channel: Optional[grpc.Channel] = None
_stub: Optional[pb_grpc.ExecutorServiceStub] = None


def _ensure_channel() -> pb_grpc.ExecutorServiceStub:
    global _channel, _stub
    if _channel is None:
        _channel = grpc.insecure_channel(EXECUTOR_ADDR)
        _stub = pb_grpc.ExecutorServiceStub(_channel)
    assert _stub is not None
    return _stub


def close() -> None:
    """Close the gRPC channel."""
    global _channel, _stub
    if _channel is not None:
        _channel.close()
        _channel = None
        _stub = None


def health() -> dict[str, Any]:
    """Check executor service health."""
    stub = _ensure_channel()
    resp = stub.Health(pb.HealthRequest(), timeout=5)
    return {"ok": resp.ok, "service": resp.service, "version": resp.version}


def preview(
    command: str,
    cwd: str = "/tmp",
    workspace_dir: str = "/tmp",
    permission_level: str = "",
    requires_approval: bool = False,
    approval_id: str = "",
    approval_token: str = "",
) -> dict[str, Any]:
    """Preview whether a command is allowed without executing it."""
    stub = _ensure_channel()
    resp = stub.PreviewTool(pb.PreviewRequest(
        command=command,
        cwd=cwd,
        workspace_dir=workspace_dir,
        permission_level=permission_level,
        requires_approval=requires_approval,
        approval_id=approval_id,
        approval_token=approval_token,
    ), timeout=10)
    return {
        "allowed": resp.allowed,
        "permission_level": resp.permission_level,
        "requires_approval": resp.requires_approval,
        "error_code": resp.error_code,
        "message": resp.message,
        "reasons": list(resp.reasons),
        "workspace_dir": resp.workspace_dir,
        "cwd": resp.cwd,
    }


def execute(
    command: str,
    cwd: str = "/tmp",
    workspace_dir: str = "/tmp",
    timeout_ms: int = 120000,
    run_id: str = "",
    approval_token: str = "",
    permission_level: str = "shell_safe",
    requires_approval: bool = False,
    approval_id: str = "",
    max_stdout_chars: int = 0,
    max_stderr_chars: int = 0,
) -> dict[str, Any]:
    """Execute a command via the executor service."""
    stub = _ensure_channel()
    resp = stub.ExecuteTool(pb.ExecuteRequest(
        command=command,
        cwd=cwd,
        workspace_dir=workspace_dir,
        timeout_ms=timeout_ms,
        run_id=run_id,
        approval_token=approval_token,
        permission_level=permission_level,
        requires_approval=requires_approval,
        approval_id=approval_id,
        max_stdout_chars=max_stdout_chars,
        max_stderr_chars=max_stderr_chars,
    ), timeout=10)
    return _run_to_dict(resp)


def get_tool_run(run_id: str) -> dict[str, Any]:
    """Get the current state of a tool run."""
    stub = _ensure_channel()
    resp = stub.GetToolRun(pb.GetToolRunRequest(id=run_id), timeout=5)
    return _run_to_dict(resp)


def stream_events(
    run_id: str,
    after_cursor: int = 0,
    callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> list[dict[str, Any]]:
    """Stream events for a tool run. Returns collected events."""
    stub = _ensure_channel()
    stream = stub.StreamToolRunEvents(pb.StreamEventsRequest(
        run_id=run_id,
        after_cursor=after_cursor,
    ))
    events: list[dict[str, Any]] = []
    try:
        for ev in stream:
            event: dict[str, Any] = {
                "seq": ev.seq,
                "type": ev.type,
                "timestamp": ev.timestamp,
                "run_id": ev.run_id,
                "data": ev.data,
            }
            events.append(event)
            if callback:
                callback(event)
    except grpc.RpcError:
        pass
    return events


def cancel(run_id: str) -> dict[str, Any]:
    """Cancel a running tool run."""
    stub = _ensure_channel()
    resp = stub.CancelToolRun(pb.CancelRequest(run_id=run_id), timeout=5)
    return {"success": resp.success, "message": resp.message}


def run_command(
    command: str,
    cwd: str = "/tmp",
    workspace_dir: str = "/tmp",
    timeout_ms: int = 120000,
    permission_level: str = "shell_safe",
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """High-level: execute, stream events, wait for completion, return result."""
    run = execute(
        command,
        cwd=cwd,
        workspace_dir=workspace_dir,
        timeout_ms=timeout_ms,
        permission_level=permission_level,
    )

    if run["status"] == "denied":
        return run

    terminal = {"completed", "failed", "timeout", "cancelled"}
    deadline = time.time() + (timeout_ms / 1000) + 5
    cursor = 0
    while time.time() < deadline:
        events = stream_events(run["id"], after_cursor=cursor)
        for ev in events:
            cursor += 1
            if on_event:
                on_event(ev)
        current = get_tool_run(run["id"])
        if current["status"] in terminal:
            return current
        time.sleep(max(0.005, EXECUTOR_POLL_INTERVAL_SECONDS))

    return get_tool_run(run["id"])


def _run_to_dict(resp: Any) -> dict[str, Any]:
    """Convert a ToolRun protobuf to a plain dict."""
    return {
        "id": resp.id,
        "status": resp.status,
        "command": resp.command,
        "cwd": resp.cwd,
        "exit_code": resp.exit_code,
        "stdout": resp.stdout,
        "stderr": resp.stderr,
        "duration_ms": resp.duration_ms,
        "timed_out": resp.timed_out,
        "error_code": resp.error_code,
        "message": resp.message,
        "stdout_truncated": resp.stdout_truncated,
        "stderr_truncated": resp.stderr_truncated,
    }
