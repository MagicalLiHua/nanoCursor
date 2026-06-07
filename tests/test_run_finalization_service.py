from __future__ import annotations

import threading

from src.api.services import conversation_service
from src.api.services.run_finalization_service import (
    finalize_delivery_best_effort,
    finalize_conversation_for_run,
    finalize_run_registry,
    persist_terminal_session,
)


class FakeStore:
    def __init__(self):
        self.updates = []

    def update_session(self, thread_id, workspace_dir=None, **changes):
        self.updates.append((thread_id, workspace_dir, changes))
        return {"thread_id": thread_id, "workspace_dir": workspace_dir, **changes}


class FakeRun:
    def __init__(self, conversation_id: str | None = None):
        self.conversation_id = conversation_id
        self.statuses = []

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set_status(self, status):
        self.statuses.append(status)


class FakeRunManager:
    def __init__(self):
        self.finalized = []
        self.unregistered = []

    def finalize(self, thread_id, final_status):
        self.finalized.append((thread_id, final_status))

    def unregister(self, thread_id):
        self.unregistered.append(thread_id)


def test_persist_terminal_session_omits_empty_optional_fields():
    store = FakeStore()

    persist_terminal_session(
        event_store=store,
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        status="failed",
        error="boom",
        saved_messages=["msg"],
    )

    assert store.updates == [
        (
            "run-1",
            "/tmp/ws",
            {"status": "failed", "error": "boom", "saved_messages": ["msg"]},
        )
    ]


def test_finalize_conversation_for_run_syncs_owner(monkeypatch):
    calls = []

    def fake_finalize(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(conversation_service, "finalize_conversation_run", fake_finalize)
    store = FakeStore()
    active_runs = {"run-1": FakeRun(conversation_id="conv-1")}

    conversation_id = finalize_conversation_for_run(
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        status="completed",
        active_runs=active_runs,
        runs_lock=threading.RLock(),
        event_store=store,
        summary="done",
    )

    assert conversation_id == "conv-1"
    assert calls == [
        {
            "conversation_id": "conv-1",
            "thread_id": "run-1",
            "status": "completed",
            "workspace_dir": "/tmp/ws",
            "summary": "done",
            "error": "",
        }
    ]
    assert store.updates[-1][2] == {
        "conversation_id": "conv-1",
        "conversation_status": "completed",
    }


def test_finalize_conversation_for_run_noops_without_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(conversation_service, "finalize_conversation_run", lambda **kwargs: calls.append(kwargs))
    store = FakeStore()

    conversation_id = finalize_conversation_for_run(
        thread_id="run-1",
        workspace_dir="/tmp/ws",
        status="completed",
        active_runs={"run-1": FakeRun()},
        runs_lock=threading.RLock(),
        event_store=store,
    )

    assert conversation_id is None
    assert calls == []
    assert store.updates == []


def test_finalize_run_registry_marks_active_run_and_releases_manager():
    manager = FakeRunManager()
    run = FakeRun()

    result = finalize_run_registry(
        active_runs={"run-1": run},
        runs_lock=threading.RLock(),
        run_manager=manager,
        thread_id="run-1",
        final_status="completed",
    )

    assert run.statuses == ["completed"]
    assert manager.finalized == [("run-1", "completed")]
    assert manager.unregistered == ["run-1"]
    assert result["thread_id"] == "run-1"
    assert result["final_status"] == "completed"


def test_best_effort_failure_is_logged_without_raising(monkeypatch):
    from src.api.services import delivery_service, run_finalization_service

    warnings = []

    class FakeLogger:
        def warning(self, message, **kwargs):
            warnings.append((message, kwargs))

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(delivery_service, "finalize_delivery", fail_delivery)
    monkeypatch.setattr(run_finalization_service, "logger", FakeLogger())

    assert finalize_delivery_best_effort("run-1", "/tmp/ws") is False
    assert warnings[0][0] == "best_effort_failed:finalize_delivery"
    assert warnings[0][1]["extra"] == {"thread_id": "run-1", "path": "/tmp/ws"}
    assert warnings[0][1]["exc_info"][1].args == ("delivery failed",)
