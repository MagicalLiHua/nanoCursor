"""Tests for SSE broker."""

import asyncio
import json
import threading
import pytest
from unittest.mock import MagicMock

from src.api.services.event_store import EventStore
from src.api.services.sse_broker import SSEBroker, stream_events_push
from src.api.models import AgentEvent


def _make_event(thread_id: str, event_type: str = "message", content: str = "") -> AgentEvent:
    return AgentEvent(
        id=f"test-{event_type}",
        thread_id=thread_id,
        type=event_type,
        timestamp=1000.0,
        agent="lead",
        content=content,
        payload={},
    )


def test_publish_to_subscribers():
    broker = SSEBroker()
    q1 = asyncio.Queue(maxsize=256)
    q2 = asyncio.Queue(maxsize=256)
    broker.subscribe("thread-1", q1)
    broker.subscribe("thread-1", q2)

    event = _make_event("thread-1", "token", "hello")
    broker.publish("thread-1", event)

    assert not q1.empty()
    assert not q2.empty()
    assert q1.get_nowait() == event.model_dump_json()
    assert q2.get_nowait() == event.model_dump_json()


def test_publish_does_not_cross_threads():
    broker = SSEBroker()
    q1 = asyncio.Queue(maxsize=256)
    q2 = asyncio.Queue(maxsize=256)
    broker.subscribe("thread-1", q1)
    broker.subscribe("thread-2", q2)

    event = _make_event("thread-1", "token", "hello")
    broker.publish("thread-1", event)

    assert not q1.empty()
    assert q2.empty()


def test_unsubscribe():
    broker = SSEBroker()
    q = asyncio.Queue(maxsize=256)
    broker.subscribe("thread-1", q)
    assert broker.subscriber_count("thread-1") == 1

    broker.unsubscribe("thread-1", q)
    assert broker.subscriber_count("thread-1") == 0

    # Publishing after unsubscribe should not crash
    event = _make_event("thread-1")
    broker.publish("thread-1", event)
    assert q.empty()


def test_unsubscribe_nonexistent():
    broker = SSEBroker()
    q = asyncio.Queue(maxsize=256)
    # Should not raise
    broker.unsubscribe("nonexistent", q)


def test_subscriber_count():
    broker = SSEBroker()
    assert broker.subscriber_count("thread-1") == 0
    assert broker.total_subscribers() == 0

    q1 = asyncio.Queue(maxsize=256)
    q2 = asyncio.Queue(maxsize=256)
    broker.subscribe("thread-1", q1)
    broker.subscribe("thread-2", q2)

    assert broker.subscriber_count("thread-1") == 1
    assert broker.subscriber_count("thread-2") == 1
    assert broker.total_subscribers() == 2


def test_publish_to_full_queue_drops_event():
    broker = SSEBroker()
    q = asyncio.Queue(maxsize=1)
    broker.subscribe("thread-1", q)

    # Fill the queue
    q.put_nowait("filler")

    event = _make_event("thread-1")
    # Should not raise, just drop
    broker.publish("thread-1", event)

    assert q.get_nowait() == "filler"
    assert q.empty()
    assert broker.dropped_event_count("thread-1") == 1
    assert broker.dropped_event_count() == 1


def test_dropped_event_count_is_isolated_per_thread():
    broker = SSEBroker()
    q1 = asyncio.Queue(maxsize=1)
    q2 = asyncio.Queue(maxsize=1)
    broker.subscribe("thread-1", q1)
    broker.subscribe("thread-2", q2)
    q1.put_nowait("filler")
    q2.put_nowait("filler")

    broker.publish("thread-1", _make_event("thread-1"))
    broker.publish("thread-1", _make_event("thread-1"))
    broker.publish("thread-2", _make_event("thread-2"))

    assert broker.dropped_event_count("thread-1") == 2
    assert broker.dropped_event_count("thread-2") == 1
    assert broker.dropped_event_count() == 3


def test_multiple_events_in_order():
    broker = SSEBroker()
    q = asyncio.Queue(maxsize=256)
    broker.subscribe("thread-1", q)

    events = [_make_event("thread-1", "token", f"part-{i}") for i in range(5)]
    for event in events:
        broker.publish("thread-1", event)

    received = []
    while not q.empty():
        received.append(q.get_nowait())

    assert len(received) == 5
    for i, raw in enumerate(received):
        data = AgentEvent.model_validate_json(raw)
        assert data.content == f"part-{i}"


def test_publish_from_worker_thread_uses_subscriber_loop():
    async def scenario():
        broker = SSEBroker()
        queue = asyncio.Queue(maxsize=4)
        broker.subscribe("thread-1", queue)
        event = _make_event("thread-1", "message", "from worker")
        worker = threading.Thread(target=broker.publish, args=("thread-1", event))
        worker.start()
        worker.join()
        return await asyncio.wait_for(queue.get(), timeout=1)

    raw = asyncio.run(scenario())

    assert AgentEvent.model_validate_json(raw).content == "from worker"


def test_event_store_listener_adapter_publishes_persisted_event(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    broker = SSEBroker()
    queue = asyncio.Queue(maxsize=4)
    broker.subscribe("thread-1", queue)
    store.add_listener(broker.publish_event)

    event = store.append_event("thread-1", "message", content="persisted", workspace_dir=str(workspace))

    assert AgentEvent.model_validate_json(queue.get_nowait()) == event


def test_stream_subscribes_before_history_and_deduplicates_race(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    broker = SSEBroker()
    event = _make_event("thread-1", "message", "during history")
    calls = 0

    def list_events(thread_id, workspace_dir=None, after=0):
        nonlocal calls
        calls += 1
        if calls == 1:
            broker.publish(thread_id, event)
            return [event]
        return []

    monkeypatch.setattr(store, "list_events", list_events)
    monkeypatch.setattr(store, "get_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.api.services.sse_broker.get_event_store", lambda: store)
    monkeypatch.setattr("src.api.services.sse_broker.get_sse_broker", lambda: broker)

    async def collect():
        stream = stream_events_push("thread-1", str(workspace), heartbeat_interval=0.01)
        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()
        return first, second

    first, second = asyncio.run(collect())

    assert json.loads(first.split("data: ", 1)[1])["id"] == event.id
    assert second == ": heartbeat\n\n"


def test_stream_resumes_after_last_event_id(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    broker = SSEBroker()
    first = _make_event("thread-1", "message", "first")
    second = AgentEvent(
        id="second-event",
        thread_id="thread-1",
        type="done",
        timestamp=1001.0,
        agent="lead",
        content="second",
        payload={},
    )

    monkeypatch.setattr(store, "list_events", lambda *_args, **_kwargs: [first, second])
    monkeypatch.setattr(store, "get_session", lambda *_args, **_kwargs: {"status": "completed"})
    monkeypatch.setattr("src.api.services.sse_broker.get_event_store", lambda: store)
    monkeypatch.setattr("src.api.services.sse_broker.get_sse_broker", lambda: broker)

    async def collect():
        stream = stream_events_push(
            "thread-1",
            str(workspace),
            last_event_id=first.id,
        )
        return await anext(stream)

    event_text = asyncio.run(collect())

    assert "id: second-event" in event_text
    assert json.loads(event_text.split("data: ", 1)[1])["content"] == "second"
