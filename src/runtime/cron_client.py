"""gRPC client for go-cron scheduler service."""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import grpc

from src.cron.proto import cron_pb2 as pb
from src.cron.proto import cron_pb2_grpc as pb_grpc

CRON_ADDR = os.getenv("NANOCURSOR_CRON_ADDR", "localhost:50057")

_channel: Optional[grpc.Channel] = None
_stub: Optional[pb_grpc.CronServiceStub] = None


def _ensure_channel() -> pb_grpc.CronServiceStub:
    global _channel, _stub
    if _channel is None:
        _channel = grpc.insecure_channel(CRON_ADDR)
        _stub = pb_grpc.CronServiceStub(_channel)
    return _stub


def close() -> None:
    """Close the gRPC channel."""
    global _channel, _stub
    if _channel is not None:
        _channel.close()
        _channel = None
        _stub = None


def health() -> dict:
    """Check cron service health."""
    stub = _ensure_channel()
    resp = stub.Health(pb.HealthRequest(), timeout=5)
    return {"ok": resp.ok, "service": resp.service, "version": resp.version}


def create_task(
    cron_expr: str,
    prompt: str,
    recurring: bool = False,
    durable: bool = True,
) -> dict:
    """Create a new cron task."""
    stub = _ensure_channel()
    resp = stub.CreateTask(pb.CreateTaskRequest(
        cron_expr=cron_expr,
        prompt=prompt,
        recurring=recurring,
        durable=durable,
    ), timeout=5)
    return _task_to_dict(resp)


def delete_task(task_id: str) -> dict:
    """Delete a cron task."""
    stub = _ensure_channel()
    resp = stub.DeleteTask(pb.DeleteTaskRequest(task_id=task_id), timeout=5)
    return {"success": resp.success, "message": resp.message}


def list_tasks() -> list[dict]:
    """List all cron tasks."""
    stub = _ensure_channel()
    resp = stub.ListTasks(pb.ListTasksRequest(), timeout=5)
    return [_task_to_dict(t) for t in resp.tasks]


def drain_events(callback: Optional[Callable[[dict], None]] = None) -> None:
    """Blocking: streams events from the server, calling callback for each."""
    stub = _ensure_channel()
    stream = stub.DrainEvents(pb.DrainEventsRequest())
    try:
        for ev in stream:
            event = {
                "type": ev.type,
                "task_id": ev.task_id,
                "prompt": ev.prompt,
                "recurring": ev.recurring,
                "fired_at": ev.fired_at,
            }
            if callback:
                callback(event)
    except grpc.RpcError:
        pass


def drain_events_async(callback: Optional[Callable[[dict], None]] = None) -> threading.Thread:
    """Non-blocking: runs drain_events in a background daemon thread."""
    t = threading.Thread(target=drain_events, args=(callback,), daemon=True)
    t.start()
    return t


def _task_to_dict(task) -> dict:
    """Convert a Task protobuf message to a plain dict."""
    return {
        "id": task.id,
        "cron_expr": task.cron_expr,
        "prompt": task.prompt,
        "recurring": task.recurring,
        "durable": task.durable,
        "created_at": task.created_at,
        "last_fired_at": task.last_fired_at,
        "status": task.status,
    }
