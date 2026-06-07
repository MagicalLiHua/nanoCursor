"""Tests for eventstore gRPC client — requires go-eventstore running on localhost:50058."""

import os
import time
import pytest


def eventstore_available():
    try:
        from src.runtime.eventstore_client import health
        result = health()
        return result.get("ok", False)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not eventstore_available(), reason="go-eventstore not running")


class TestEventStoreHealth:
    def test_health(self):
        from src.runtime.eventstore_client import health
        result = health()
        assert result["ok"] is True
        assert result["service"] == "nanocursor-eventstore"


class TestSessionCRUD:
    def test_create_and_get(self):
        from src.runtime.eventstore_client import create_session, get_session
        session = create_session("test-t1", "hello", "/tmp")
        assert session["thread_id"] == "test-t1"
        assert session["status"] == "running"
        assert session["prompt"] == "hello"

        got = get_session("test-t1", "/tmp")
        assert got is not None
        assert got["prompt"] == "hello"

    def test_update(self):
        from src.runtime.eventstore_client import create_session, update_session
        create_session("test-t2", "hello", "/tmp")
        updated = update_session("test-t2", "/tmp", status="completed")
        assert updated["status"] == "completed"


class TestEventOperations:
    def test_append_and_list(self):
        from src.runtime.eventstore_client import append_event, list_events, count_events
        import uuid
        thread_id = f"test-t3-{uuid.uuid4().hex[:8]}"
        append_event(thread_id, "message", title="hi", workspace_dir="/tmp")
        append_event(thread_id, "done", workspace_dir="/tmp")

        events = list_events(thread_id, "/tmp")
        assert len(events) == 2
        assert events[0]["type"] == "message"
        assert events[1]["type"] == "done"

        assert count_events(thread_id, "/tmp") == 2

    def test_workspace_for_thread(self):
        from src.runtime.eventstore_client import create_session, workspace_for_thread
        create_session("test-t4", "hello", "/tmp")
        ws = workspace_for_thread("test-t4")
        assert ws is not None
