"""Observability & ops tests — request-id, diagnostics, structured logging."""

import json
import logging

from fastapi.testclient import TestClient

from src.infra.logging import StructuredFormatter, setup_structured_logging


# ---------------------------------------------------------------------------
# Request ID
# ---------------------------------------------------------------------------

class TestRequestID:
    def test_health_has_request_id(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/health")
        assert "x-request-id" in resp.headers

    def test_api_error_has_request_id(self):
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/runs/nonexistent_xyz_99999")
        assert resp.status_code == 404
        data = resp.json()
        # Either flat or nested error format
        if "error" in data:
            assert "request_id" in data["error"]
        assert "x-request-id" in resp.headers

    def test_custom_request_id_passthrough(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/health", headers={"x-request-id": "my-custom-id"})
        assert resp.headers["x-request-id"] == "my-custom-id"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_diagnostics_endpoint_returns_200(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "system" in data
        assert "workspace" in data
        assert "env" in data

    def test_diagnostics_no_api_keys_leaked(self):
        from api_server import app
        import os
        # Temporarily set a fake API key
        os.environ["TEST_API_KEY"] = "secret-value-123"
        try:
            client = TestClient(app)
            resp = client.get("/api/system/diagnostics")
            assert resp.status_code == 200
            text = resp.text.lower()
            # The value "secret-value-123" should never appear in the response
            assert "secret-value-123" not in text
        finally:
            del os.environ["TEST_API_KEY"]

    def test_diagnostics_includes_mcp_status(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "mcp" in data

    def test_diagnostics_includes_runs(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert "metrics" in data["runs"]

    def test_diagnostics_includes_errors(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "errors" in data

    def test_diagnostics_respects_workspace_dir(self, tmp_path):
        from api_server import app
        workspace = tmp_path / "custom-workspace"
        workspace.mkdir()

        client = TestClient(app)
        resp = client.get("/api/system/diagnostics", params={"workspace_dir": str(workspace)})

        assert resp.status_code == 200
        data = resp.json()
        assert data["workspace"]["path"] == str(workspace.resolve())
        assert data["runs"]["metrics"]["total_runs"] == 0


# ---------------------------------------------------------------------------
# Doctor endpoint
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_doctor_returns_checks(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/system/doctor")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data
        assert "ok" in data
        assert "workspace_dir" in data
        check_ids = {c["id"] for c in data["checks"]}
        assert "python" in check_ids
        assert "workspace" in check_ids


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    def test_formatter_outputs_json(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="nanoCursor", level=logging.INFO, pathname="", lineno=0,
            msg="test_event", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["event"] == "test_event"
        assert data["level"] == "INFO"
        assert "time" in data

    def test_formatter_includes_extra_fields(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="nanoCursor", level=logging.WARNING, pathname="", lineno=0,
            msg="slow_request", args=(), exc_info=None,
        )
        record.request_id = "req_abc123"
        record.duration_ms = 1200
        record.path = "/api/test"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["request_id"] == "req_abc123"
        assert data["duration_ms"] == 1200

    def test_setup_structured_logging_returns_logger(self):
        logger = setup_structured_logging("WARNING")
        assert logger.level == logging.WARNING
        assert len(logger.handlers) >= 1
