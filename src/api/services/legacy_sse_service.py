"""Legacy queue-backed SSE response helpers."""

from __future__ import annotations

import json
import queue
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def stream_legacy_run_events(thread_id: str, active_runs: dict[str, Any]) -> StreamingResponse:
    """Stream events from the legacy in-memory run queue."""
    run_info = active_runs.get(thread_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    event_queue = run_info["queue"]

    def event_generator():
        while True:
            try:
                item = event_queue.get(timeout=300)
                if item is None:
                    break
                event_type = json.loads(item).get("type", "message")
                yield f"event: {event_type}\ndata: {item}\n\n"
                if event_type in ("done", "error"):
                    break
            except queue.Empty:
                yield ": heartbeat\n\n"
            except Exception as exc:
                payload = json.dumps({"type": "error", "message": str(exc)})
                yield f"event: error\ndata: {payload}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
