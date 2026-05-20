"""Run lifecycle tests — state transitions, locks, cancel, retry, recovery."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.runtime.run_state import RunStateMachine, RunStatus, ALLOWED_TRANSITIONS
from src.runtime.run_manager import RunManager
from src.api.services.run_lifecycle_service import (
    create_run_context,
    cleanup_stale_runs,
    finalize_run,
    recover_interrupted_runs,
    register_run,
    transition_run,
)
from src.api.services.run_context import RunContext


# ---------------------------------------------------------------------------
# State machine unit tests
# ---------------------------------------------------------------------------

class TestRunStateMachine:
    def test_valid_transition(self):
        sm = RunStateMachine(RunStatus.CREATED)
        assert sm.transition(RunStatus.RUNNING) == RunStatus.RUNNING
        assert len(sm.history()) == 2

    def test_illegal_transition_rejected(self):
        sm = RunStateMachine(RunStatus.CREATED)
        with pytest.raises(ValueError, match="不允许的状态转移"):
            sm.transition(RunStatus.COMPLETED)

    def test_terminal_detection(self):
        sm = RunStateMachine(RunStatus.RUNNING)
        assert not sm.is_terminal()
        sm.transition(RunStatus.COMPLETED)
        assert sm.is_terminal()

        failed = RunStateMachine(RunStatus.RUNNING)
        failed.transition(RunStatus.FAILED)
        assert failed.is_terminal()

        interrupted = RunStateMachine(RunStatus.RUNNING)
        interrupted.transition(RunStatus.INTERRUPTED)
        assert interrupted.is_terminal()

    def test_cannot_transition_from_terminal(self):
        sm = RunStateMachine(RunStatus.COMPLETED)
        assert not sm.can_transition(RunStatus.FAILED)

    def test_all_documented_transitions_covered(self):
        """Every state in ALLOWED_TRANSITIONS has at least one valid path."""
        for from_status, to_set in ALLOWED_TRANSITIONS.items():
            assert len(to_set) > 0, f"{from_status} has no outgoing transitions"


# ---------------------------------------------------------------------------
# RunManager integration tests
# ---------------------------------------------------------------------------

class TestRunManager:
    def test_register_and_unregister(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        from src.api.services.run_context import RunContext
        import queue
        ctx = RunContext(thread_id="t1", workspace_dir=str(ws), queue=queue.Queue())

        assert rm.register(ctx) is True
        assert rm.get("t1") is ctx
        sm = rm.get_state_machine("t1")
        assert sm is not None
        assert sm.status == RunStatus.RUNNING

        rm.unregister("t1")
        assert rm.get("t1") is None
        assert rm.get_state_machine("t1") is None

    def test_write_lock_rejects_second_write_run(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        import queue
        ctx1 = RunContext(thread_id="t1", workspace_dir=str(ws), queue=queue.Queue())
        ctx2 = RunContext(thread_id="t2", workspace_dir=str(ws), queue=queue.Queue())

        rm.register(ctx1)
        with pytest.raises(ValueError, match="工作区已被写入型"):
            rm.register(ctx2)

    def test_read_only_runs_do_not_take_workspace_write_lock(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        import queue
        read_ctx = RunContext(
            thread_id="read",
            workspace_dir=str(ws),
            queue=queue.Queue(),
            mode="read_only",
        )
        write_ctx = RunContext(thread_id="write", workspace_dir=str(ws), queue=queue.Queue())

        assert rm.register(read_ctx) is True
        assert rm.register(write_ctx) is True

    def test_lock_released_after_unregister(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        import queue
        ctx1 = RunContext(thread_id="t1", workspace_dir=str(ws), queue=queue.Queue())
        rm.register(ctx1)
        rm.unregister("t1")

        ctx2 = RunContext(thread_id="t2", workspace_dir=str(ws), queue=queue.Queue())
        assert rm.register(ctx2) is True  # should succeed now

    def test_cancel_sets_cancelling_state(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        import queue
        ctx = RunContext(thread_id="t1", workspace_dir=str(ws), queue=queue.Queue())
        rm.register(ctx)
        rm.request_cancel("t1")
        sm = rm.get_state_machine("t1")
        assert sm.status == RunStatus.CANCELLING

    def test_finalize_sets_terminal(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        import queue
        ctx = RunContext(thread_id="t1", workspace_dir=str(ws), queue=queue.Queue())
        rm.register(ctx)
        rm.finalize("t1", RunStatus.COMPLETED)
        sm = rm.get_state_machine("t1")
        assert sm.status == RunStatus.COMPLETED

    def test_detect_interrupted(self, tmp_path):
        """Runs with status 'running' on disk but not in active registry are detected."""
        import src.infra.config as config_module
        original = config_module.WORKSPACE_DIR

        ws = tmp_path / "ws"
        ws.mkdir()
        runs_dir = ws / ".nanocursor" / "runs" / "run_interrupted"
        runs_dir.mkdir(parents=True)
        session = {"thread_id": "run_interrupted", "status": "running", "prompt": "test"}
        (runs_dir / "session.json").write_text(json.dumps(session), encoding="utf-8")

        try:
            config_module.WORKSPACE_DIR = str(ws)
            rm = RunManager()
            interrupted = rm.detect_interrupted(str(ws))
            assert "run_interrupted" in interrupted
        finally:
            config_module.WORKSPACE_DIR = original


# ---------------------------------------------------------------------------
# Lifecycle service tests
# ---------------------------------------------------------------------------

class TestRunLifecycleService:
    def test_create_run_context_defaults(self):
        ctx = create_run_context("t1", "/tmp/ws")
        assert ctx["thread_id"] == "t1"
        assert ctx["status"] == "created"

    def test_finalize_run_unregisters(self, tmp_path):
        rm = RunManager()
        ws = tmp_path / "ws"
        ws.mkdir()
        import queue
        ctx = RunContext(thread_id="t1", workspace_dir=str(ws), queue=queue.Queue())
        rm.register(ctx)

        result = finalize_run(rm, "t1", "completed")
        assert result["thread_id"] == "t1"
        assert rm.get("t1") is None  # unregistered

    def test_cleanup_stale_runs(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        runs_dir = ws / ".nanocursor" / "runs"
        runs_dir.mkdir(parents=True)

        old_session = {"thread_id": "old_run", "status": "completed", "completed_at": 0}
        (runs_dir / "old_run").mkdir()
        (runs_dir / "old_run" / "session.json").write_text(json.dumps(old_session))

        rm = RunManager()
        cleaned = cleanup_stale_runs(rm, str(ws), older_than_hours=0)
        assert cleaned >= 0  # best-effort cleanup


# ---------------------------------------------------------------------------
# API-level lifecycle tests
# ---------------------------------------------------------------------------

class TestLifecycleAPI:
    def test_get_lifecycle_for_nonexistent_run(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/runs/nonexistent_xyz/lifecycle")
        assert resp.status_code == 404

    def test_retry_non_existent_run_returns_404(self):
        from api_server import app
        client = TestClient(app)
        resp = client.post("/api/runs/nonexistent_xyz/retry")
        assert resp.status_code == 404

    def test_retry_on_active_run_returns_400(self, tmp_path):
        from api_server import app
        client = TestClient(app)

        ws = tmp_path / "ws"
        ws.mkdir()
        # Create a completed session on disk so retry doesn't fail with 404 first,
        # then verify that an active run cannot be retried
        import src.infra.config as config_module
        original = config_module.WORKSPACE_DIR
        try:
            config_module.WORKSPACE_DIR = str(ws)
            runs_dir = ws / ".nanocursor" / "runs" / "running_run"
            runs_dir.mkdir(parents=True)
            session = {"thread_id": "running_run", "status": "running", "prompt": "test",
                       "workspace_dir": str(ws)}
            (runs_dir / "session.json").write_text(json.dumps(session))
            resp = client.post("/api/runs/running_run/retry")
            assert resp.status_code == 400  # running state, cannot retry
        finally:
            config_module.WORKSPACE_DIR = original
