from __future__ import annotations

import queue
import threading

from src.api.services.deterministic_run_service import run_deterministic_worker
from src.api.services.event_store import EventStore
from src.api.services.run_context import RunContext
from src.api.services.run_rate_limit_service import (
    check_run_start_rate_limit,
    clear_run_start_rate_limit,
)
from src.api.services.runtime_registry_service import RuntimeRegistry
from src.api.services.workflow_thread_service import (
    start_resumed_workflow_thread,
    start_workflow_thread,
)
from src.runtime.run_manager import RunManager


def _registered_runtime(tmp_path, thread_id: str = "deterministic-run"):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = RuntimeRegistry(RunManager(), EventStore())
    context = RunContext(thread_id=thread_id, workspace_dir=str(workspace), queue=queue.Queue())
    registry.run_manager.register(context)
    registry.event_store.create_session(thread_id, "deterministic", str(workspace), status="running")
    return registry, context, workspace


def test_deterministic_worker_finalizes_and_unregisters_run(tmp_path, monkeypatch):
    registry, context, workspace = _registered_runtime(tmp_path)
    monkeypatch.setattr(
        "src.api.services.deterministic_run_service.finalize_delivery_best_effort",
        lambda *_args, **_kwargs: True,
    )

    run_deterministic_worker(
        thread_id=context.thread_id,
        workspace_dir=str(workspace),
        execute=lambda status_callback: status_callback("completed"),
        error_title="should not fail",
        registry=registry,
    )

    assert registry.run_manager.get(context.thread_id) is None
    assert registry.event_store.get_session(context.thread_id, str(workspace))["status"] == "completed"


def test_deterministic_worker_persists_failure_and_unregisters_run(tmp_path, monkeypatch):
    registry, context, workspace = _registered_runtime(tmp_path)
    monkeypatch.setattr(
        "src.api.services.deterministic_run_service.finalize_delivery_best_effort",
        lambda *_args, **_kwargs: True,
    )

    def fail(_status_callback):
        raise RuntimeError("deterministic failure")

    run_deterministic_worker(
        thread_id=context.thread_id,
        workspace_dir=str(workspace),
        execute=fail,
        error_title="Worker failed",
        error_payload={"mode": "test"},
        registry=registry,
    )

    assert registry.run_manager.get(context.thread_id) is None
    session = registry.event_store.get_session(context.thread_id, str(workspace))
    assert session["status"] == "failed"
    assert session["error"] == "deterministic failure"
    events = registry.event_store.list_events(context.thread_id, str(workspace))
    assert events[-1].title == "Worker failed"
    assert events[-1].payload["mode"] == "test"


def test_run_start_rate_limit_rejects_active_and_rapid_duplicate_runs():
    lock = threading.RLock()
    active_runs = {}
    clear_run_start_rate_limit()
    try:
        allowed, _ = check_run_start_rate_limit(
            "run-1",
            active_runs=active_runs,
            runs_lock=lock,
            clock=lambda: 100.0,
        )
        assert allowed is True

        allowed, message = check_run_start_rate_limit(
            "run-1",
            active_runs=active_runs,
            runs_lock=lock,
            clock=lambda: 101.0,
        )
        assert allowed is False
        assert "启动过于频繁" in message

        active_runs["active"] = {"status": "running"}
        allowed, message = check_run_start_rate_limit(
            "active",
            active_runs=active_runs,
            runs_lock=lock,
            clock=lambda: 200.0,
        )
        assert allowed is False
        assert "已有一个工作流" in message
    finally:
        clear_run_start_rate_limit()


def test_workflow_thread_service_owns_normal_and_resume_thread_start(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = RunContext(thread_id="thread-boundary", workspace_dir=str(workspace), queue=queue.Queue())
    calls = []

    normal = start_workflow_thread(
        thread_id=context.thread_id,
        initial_messages=["prompt"],
        workspace_dir=str(workspace),
        run_context=context,
        workflow_runner=lambda *args: calls.append(("normal", args)),
    )
    normal.join(timeout=2)

    resumed = start_resumed_workflow_thread(
        thread_id=context.thread_id,
        messages=["history"],
        system="system",
        workspace_dir=str(workspace),
        run_context=context,
        workflow_runner=lambda *args: calls.append(("resume", args)),
    )
    resumed.join(timeout=2)

    assert calls == [
        ("normal", (context.thread_id, ["prompt"], str(workspace))),
        ("resume", (context.thread_id, ["history"], "system", str(workspace))),
    ]
    assert context.thread is resumed
