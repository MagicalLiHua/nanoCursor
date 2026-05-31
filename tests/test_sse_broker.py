"""Tests for SSE broker."""

import asyncio
import pytest
from unittest.mock import MagicMock

from src.api.services.sse_broker import SSEBroker
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
