"""Contract freeze tests — verify core routes, event types, error codes, and state machine."""

import json

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Core routes must exist
# ---------------------------------------------------------------------------

CORE_ROUTES = [
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/version"),
    ("GET", "/api/run/{thread_id}/events"),
    ("GET", "/api/runs/active"),
    ("POST", "/api/run"),
    ("POST", "/api/runs/{thread_id}/cancel"),
    ("POST", "/api/runs/{thread_id}/retry"),
]

R1_R6_ROUTES = [
    ("GET", "/api/runs/{thread_id}/delivery"),
    ("POST", "/api/runs/{thread_id}/delivery/finalize"),
    ("GET", "/api/runs/{thread_id}/changes"),
    ("POST", "/api/runs/{thread_id}/changes/collect"),
    ("GET", "/api/runs/{thread_id}/ledger"),
    ("GET", "/api/runs/{thread_id}/steps"),
    ("GET", "/api/runs/{thread_id}/tools"),
    ("GET", "/api/runs/{thread_id}/failures"),
    ("POST", "/api/runs/{thread_id}/failures/{failure_id}/remediate"),
    ("POST", "/api/runs/{thread_id}/actions/check"),
    ("GET", "/api/runs/{thread_id}/audit"),
]


class TestCoreRoutesExist:
    def test_core_routes_registered(self):
        from api_server import app
        route_paths = set()
        for route in app.routes:
            if hasattr(route, 'path') and hasattr(route, 'methods'):
                for method in route.methods:
                    route_paths.add((method, route.path))

        for method, path in CORE_ROUTES:
            assert (method, path) in route_paths, f"Missing core route: {method} {path}"

    def test_r1_r6_routes_registered(self):
        from api_server import app
        route_paths = set()
        for route in app.routes:
            if hasattr(route, 'path') and hasattr(route, 'methods'):
                for method in route.methods:
                    route_paths.add((method, route.path))

        for method, path in R1_R6_ROUTES:
            assert (method, path) in route_paths, f"Missing R1-R6 route: {method} {path}"


class TestStaticRoutesNotShadowed:
    def test_runs_active_not_shadowed(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/runs/active")
        assert resp.status_code == 200
        assert "active_runs" in resp.json()

    def test_runs_route_not_shadowing_specific_routes(self):
        """Verify /api/runs/{thread_id} doesn't shadow /api/runs/active."""
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        # If active was shadowed by {thread_id}, this would match "active" as a thread_id
        resp = client.get("/api/runs/active")
        assert resp.status_code == 200
        assert "active_runs" in resp.json()

    def test_delivery_route_not_shadowed_by_thread_detail(self):
        from api_server import app
        from src.api.services.event_store import EventStore
        import src.infra.config as cfg
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventStore()
            store.create_session("shadow_test", "test", tmpdir, status="completed")
            old = cfg.WORKSPACE_DIR
            try:
                cfg.WORKSPACE_DIR = tmpdir
                client = TestClient(app)
                resp = client.get("/api/runs/shadow_test/delivery")
                # Should return delivery, not "Run doesn't exist" or 404
                assert resp.status_code == 200
                data = resp.json()
                assert "thread_id" in data
            finally:
                cfg.WORKSPACE_DIR = old


class TestCoreErrorCodes:
    def test_error_codes_stable(self):
        from src.api.errors import ApiErrorCode
        codes = {c.value for c in ApiErrorCode}
        expected = {
            "invalid_request",
            "resource_not_found",
            "workspace_not_open",
            "workspace_path_invalid",
            "run_conflict",
            "run_not_active",
            "approval_required",
            "action_requires_confirmation",
            "config_missing",
            "mcp_config_invalid",
            "skill_invalid",
            "internal_error",
        }
        assert codes == expected, f"Error codes changed! Expected {expected}, got {codes}"

    def test_error_response_format(self):
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/runs/nonexistent_run_contract_test/delivery")
        assert resp.status_code == 404
        data = resp.json()
        if "error" in data:
            assert "code" in data["error"]
            assert "message" in data["error"]
            assert "request_id" in data["error"]


class TestRunStateMachine:
    def test_all_states_present(self):
        from src.runtime.run_state import RunStatus
        states = {s.value for s in RunStatus}
        required = {
            "created", "planning", "waiting_approval", "running",
            "validating", "cancelling", "completed", "failed",
            "interrupted", "cancelled", "recovering",
        }
        assert states == required, f"States changed! Expected {required}, got {states}"
        # CANCEL_REQUESTED is alias for cancelling
        assert RunStatus.CANCEL_REQUESTED == RunStatus.CANCELLING

    def test_terminal_states(self):
        from src.runtime.run_state import TERMINAL_STATUSES, RunStatus
        assert RunStatus.COMPLETED in TERMINAL_STATUSES
        assert RunStatus.CANCELLED in TERMINAL_STATUSES
        assert RunStatus.FAILED in TERMINAL_STATUSES
        assert RunStatus.INTERRUPTED in TERMINAL_STATUSES

    def test_valid_transitions(self):
        from src.runtime.run_state import RunStateMachine, RunStatus
        sm = RunStateMachine(RunStatus.CREATED)
        sm.transition(RunStatus.RUNNING)
        assert sm.status == RunStatus.RUNNING

    def test_invalid_transition_raises(self):
        from src.runtime.run_state import RunStateMachine, RunStatus
        import pytest
        sm = RunStateMachine(RunStatus.CREATED)
        with pytest.raises(ValueError):
            sm.transition(RunStatus.FAILED)  # CREATED → FAILED not allowed

    def test_is_terminal(self):
        from src.runtime.run_state import RunStateMachine, RunStatus
        sm = RunStateMachine(RunStatus.CREATED)
        sm.transition(RunStatus.RUNNING)
        sm.transition(RunStatus.COMPLETED)
        assert sm.is_terminal()

    def test_history_records_transitions(self):
        from src.runtime.run_state import RunStateMachine, RunStatus
        sm = RunStateMachine(RunStatus.CREATED)
        sm.transition(RunStatus.PLANNING)
        sm.transition(RunStatus.RUNNING)
        assert sm.history() == ["created", "planning", "running"]

    def test_all_modes_present(self):
        from src.runtime.run_state import RunMode
        modes = {m.value for m in RunMode}
        expected = {"normal", "conversation", "demo", "benchmark", "eval", "remediation"}
        assert modes == expected


class TestEventTypes:
    def test_event_schema_stable(self):
        from src.runtime.event_schema import RunEvent, ToolCallPayload
        evt = RunEvent(
            id="evt_1", thread_id="run_1", type="tool_call_finished",
            title="test", content="ok", agent="coder",
        )
        d = evt.model_dump()
        assert d["schema_version"] == "1.0"

    def test_tool_call_payload_fields(self):
        from src.runtime.event_schema import ToolCallPayload
        p = ToolCallPayload(tool="write_file", output="ok")
        d = p.model_dump()
        assert d["tool"] == "write_file"
        assert "input" in d
        assert "ok" in d
