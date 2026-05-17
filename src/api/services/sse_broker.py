"""
Real-time event broker for push-based SSE streaming.

Replaces the file-polling approach in stream_agenthub_events with
asyncio.Queue-based push. Events are broadcast to all connected
clients immediately when they occur.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from src.api.models import AgentEvent
from src.api.services.event_store import get_event_store


class SSEBroker:
    """Push-based event broker for real-time SSE streaming."""

    def __init__(self):
        # Map thread_id -> set of asyncio.Queues
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, thread_id: str, queue: asyncio.Queue) -> None:
        """Register a client queue for a thread's events."""
        with self._lock:
            if thread_id not in self._subscribers:
                self._subscribers[thread_id] = set()
            self._subscribers[thread_id].add(queue)

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue) -> None:
        """Remove a client queue."""
        with self._lock:
            subs = self._subscribers.get(thread_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[thread_id]

    def publish(self, thread_id: str, event: AgentEvent) -> None:
        """Push an event to all subscribers of a thread."""
        with self._lock:
            subs = self._subscribers.get(thread_id, set())
            # Copy to avoid mutation during iteration
            subs = set(subs)

        event_json = event.model_dump_json()
        for q in subs:
            try:
                q.put_nowait(event_json)
            except asyncio.QueueFull:
                pass  # Drop event if client is too slow

    def subscriber_count(self, thread_id: str) -> int:
        """Return the number of active subscribers for a thread."""
        with self._lock:
            return len(self._subscribers.get(thread_id, set()))

    def total_subscribers(self) -> int:
        """Return total number of subscribers across all threads."""
        with self._lock:
            return sum(len(s) for s in self._subscribers.values())


# Global singleton
_sse_broker: SSEBroker | None = None


def get_sse_broker() -> SSEBroker:
    """Get or create the global SSE broker."""
    global _sse_broker
    if _sse_broker is None:
        _sse_broker = SSEBroker()
    return _sse_broker


async def stream_events_push(
    thread_id: str,
    workspace_dir: str,
    heartbeat_interval: float = 15.0,
):
    """
    Push-based SSE event generator.

    Yields SSE-formatted strings for StreamingResponse.
    Uses an asyncio.Queue that receives events in real-time
    from the SSEBroker.

    Also replays historical events first (for reconnecting clients).
    """
    store = get_event_store()
    broker = get_sse_broker()

    # First, replay any historical events
    history = store.list_events(thread_id, workspace_dir)
    for event in history:
        yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"

    # If the session is already in a terminal state, stop
    session = store.get_session(thread_id, workspace_dir)
    if session and session.get("status") in ("completed", "failed", "cancelled"):
        return

    # Subscribe to real-time events
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    broker.subscribe(thread_id, queue)

    try:
        cursor = len(history)
        while True:
            try:
                event_json = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                # Parse to get event type
                event_data = json.loads(event_json)
                event_type = event_data.get("type", "message")
                yield f"event: {event_type}\ndata: {event_json}\n\n"
                cursor += 1

                # Check if this is a terminal event
                if event_type in ("done", "error"):
                    # Give a moment for any trailing events
                    try:
                        for _ in range(5):
                            event_json = queue.get_nowait()
                            event_data = json.loads(event_json)
                            event_type2 = event_data.get("type", "message")
                            yield f"event: {event_type2}\ndata: {event_json}\n\n"
                    except asyncio.QueueEmpty:
                        pass
                    return

            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                yield ": heartbeat\n\n"

                # Also check if the run has completed externally
                session_now = store.get_session(thread_id, workspace_dir)
                if session_now and session_now.get("status") in ("completed", "failed", "cancelled"):
                    return
    finally:
        broker.unsubscribe(thread_id, queue)


# ========== Patch EventStore to auto-publish ==========

def patch_event_store_for_push():
    """
    Monkey-patch EventStore.append_event to also publish to the SSE broker.
    This ensures all events are pushed in real-time without changing callers.
    """
    store = get_event_store()
    broker = get_sse_broker()
    original_append = store.append_event

    def append_and_publish(*args, **kwargs):
        event = original_append(*args, **kwargs)
        broker.publish(event.thread_id, event)
        return event

    store.append_event = append_and_publish


__all__ = [
    "SSEBroker", "get_sse_broker", "stream_events_push",
    "patch_event_store_for_push",
]
