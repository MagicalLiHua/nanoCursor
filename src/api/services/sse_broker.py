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
from src.infra.logging import get_logger


logger = get_logger()


class SSEBroker:
    """Push-based event broker for real-time SSE streaming."""

    def __init__(self):
        # Map thread_id -> queue and the event loop that owns it.
        self._subscribers: dict[
            str, dict[asyncio.Queue, asyncio.AbstractEventLoop | None]
        ] = {}
        self._dropped_events: dict[str, int] = {}
        self._lock = threading.Lock()

    def subscribe(
        self,
        thread_id: str,
        queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Register a client queue for a thread's events."""
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        with self._lock:
            if thread_id not in self._subscribers:
                self._subscribers[thread_id] = {}
            self._subscribers[thread_id][queue] = loop

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue) -> None:
        """Remove a client queue."""
        with self._lock:
            subs = self._subscribers.get(thread_id)
            if subs:
                subs.pop(queue, None)
                if not subs:
                    del self._subscribers[thread_id]

    def publish(self, thread_id: str, event: AgentEvent) -> None:
        """Push an event to all subscribers of a thread."""
        with self._lock:
            subs = tuple(self._subscribers.get(thread_id, {}).items())

        event_json = event.model_dump_json()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        for queue, owner_loop in subs:
            if owner_loop is not None and owner_loop.is_running() and owner_loop is not current_loop:
                try:
                    owner_loop.call_soon_threadsafe(
                        self._enqueue,
                        thread_id,
                        queue,
                        event_json,
                    )
                except RuntimeError:
                    self.unsubscribe(thread_id, queue)
                continue
            self._enqueue(thread_id, queue, event_json)

    def publish_event(self, event: AgentEvent) -> None:
        """EventStore listener adapter for publishing one persisted event."""
        self.publish(event.thread_id, event)

    def _enqueue(self, thread_id: str, queue: asyncio.Queue, event_json: str) -> None:
        """Put one event into a subscriber queue on the queue owner's loop."""
        try:
            queue.put_nowait(event_json)
        except asyncio.QueueFull:
            with self._lock:
                dropped = self._dropped_events.get(thread_id, 0) + 1
                self._dropped_events[thread_id] = dropped
            # Log at powers of two to keep a persistently slow client from
            # flooding logs while still making data loss observable.
            if dropped & (dropped - 1) == 0:
                logger.warning(
                    "sse_event_dropped",
                    extra={"thread_id": thread_id, "dropped_events": dropped},
                )

    def subscriber_count(self, thread_id: str) -> int:
        """Return the number of active subscribers for a thread."""
        with self._lock:
            return len(self._subscribers.get(thread_id, set()))

    def total_subscribers(self) -> int:
        """Return total number of subscribers across all threads."""
        with self._lock:
            return sum(len(s) for s in self._subscribers.values())

    def dropped_event_count(self, thread_id: str | None = None) -> int:
        """Return dropped-event count for one thread or the whole broker."""
        with self._lock:
            if thread_id is not None:
                return self._dropped_events.get(thread_id, 0)
            return sum(self._dropped_events.values())


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
    last_event_id: str | None = None,
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

    # Subscribe before reading history so events written during replay cannot
    # fall into a history/live gap. Queued duplicates are filtered by event id.
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    broker.subscribe(thread_id, queue)

    try:
        full_history = store.list_events(thread_id, workspace_dir)
        delivered_ids = {event.id for event in full_history}
        persisted_cursor = len(full_history)
        history = full_history
        if last_event_id:
            for index, event in enumerate(full_history):
                if event.id == last_event_id:
                    history = full_history[index + 1 :]
                    break
        for event in history:
            yield _format_event(event)
            if event.type in ("done", "error"):
                return

        session = store.get_session(thread_id, workspace_dir)
        if session and session.get("status") in ("completed", "failed", "cancelled"):
            catch_up = store.list_events(thread_id, workspace_dir, after=persisted_cursor)
            for event in catch_up:
                if event.id in delivered_ids:
                    continue
                delivered_ids.add(event.id)
                yield _format_event(event)
            return

        while True:
            try:
                event_json = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                # Parse to get event type
                event_data = json.loads(event_json)
                event_type = event_data.get("type", "message")
                event_id = str(event_data.get("id", ""))
                if event_id and event_id in delivered_ids:
                    continue
                if event_id:
                    delivered_ids.add(event_id)
                yield _format_raw_event(event_type, event_id, event_json)

                # Check if this is a terminal event
                if event_type in ("done", "error"):
                    # Give a moment for any trailing events
                    try:
                        for _ in range(5):
                            event_json = queue.get_nowait()
                            event_data = json.loads(event_json)
                            event_type2 = event_data.get("type", "message")
                            event_id2 = str(event_data.get("id", ""))
                            if event_id2 and event_id2 in delivered_ids:
                                continue
                            if event_id2:
                                delivered_ids.add(event_id2)
                            yield _format_raw_event(event_type2, event_id2, event_json)
                    except asyncio.QueueEmpty:
                        pass
                    return

            except asyncio.TimeoutError:
                # Catch up from persistence before the heartbeat. This repairs
                # persisted events dropped by a slow subscriber queue.
                catch_up = store.list_events(thread_id, workspace_dir, after=persisted_cursor)
                persisted_cursor += len(catch_up)
                terminal_seen = False
                for event in catch_up:
                    if event.id in delivered_ids:
                        continue
                    delivered_ids.add(event.id)
                    yield _format_event(event)
                    terminal_seen = terminal_seen or event.type in ("done", "error")
                if terminal_seen:
                    return

                # Also check if the run has completed externally
                session_now = store.get_session(thread_id, workspace_dir)
                if session_now and session_now.get("status") in ("completed", "failed", "cancelled"):
                    return
                yield ": heartbeat\n\n"
    finally:
        broker.unsubscribe(thread_id, queue)


def _format_event(event: AgentEvent) -> str:
    return _format_raw_event(event.type, event.id, event.model_dump_json())


def _format_raw_event(event_type: str, event_id: str, event_json: str) -> str:
    event_id_line = f"id: {event_id}\n" if event_id else ""
    return f"{event_id_line}event: {event_type}\ndata: {event_json}\n\n"


def register_event_store_push() -> bool:
    """Register the broker as an explicit EventStore listener."""
    store = get_event_store()
    broker = get_sse_broker()
    return store.add_listener(broker.publish_event)


def patch_event_store_for_push() -> bool:
    """Backward-compatible alias for the former monkey-patch initializer."""
    return register_event_store_push()


__all__ = [
    "SSEBroker", "get_sse_broker", "stream_events_push",
    "patch_event_store_for_push", "register_event_store_push",
]
