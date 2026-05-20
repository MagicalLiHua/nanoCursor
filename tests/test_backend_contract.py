"""Backend contract tests — verify no duplicate routes, error format, core routes."""

from collections import defaultdict

from fastapi.testclient import TestClient


def test_no_duplicate_routes():
    """Ensure no duplicate (path, method) registrations exist."""
    from api_server import app

    routes = defaultdict(list)
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = tuple(sorted(getattr(route, "methods", []) or []))
        if path.startswith("/api/"):
            routes[(path, methods)].append(getattr(route, "name", str(route)))

    duplicates = {k: v for k, v in routes.items() if len(v) > 1}
    assert duplicates == {}, f"Duplicate routes found: {duplicates}"


def test_all_api_routes_return_json_for_errors():
    """API routes return JSON-parseable error bodies for bad requests."""
    from api_server import app

    client = TestClient(app, raise_server_exceptions=False)

    # Non-existent run should return JSON error
    resp = client.get("/api/runs/nonexistent_run_12345")
    assert resp.status_code in (200, 404)
    data = resp.json()
    # Either a valid response or an error with recognizable structure
    assert isinstance(data, (dict, list))


def test_core_routes_registered():
    """Core API surface is registered."""
    from api_server import app

    client = TestClient(app)

    # Health
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in (200, 503)

    # Workspace
    assert client.get("/api/workspace/health").status_code == 200

    # System
    assert client.get("/api/system/version").status_code == 200
    assert client.get("/api/system/doctor").status_code == 200

    # Capabilities
    assert client.get("/api/capabilities").status_code == 200

    # Metrics
    assert client.get("/api/metrics").status_code == 200


def test_health_endpoints():
    """Health and readiness probes return expected fields."""
    from api_server import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_request_id_header():
    """Every response includes x-request-id header."""
    from api_server import app

    client = TestClient(app)
    resp = client.get("/health")
    assert "x-request-id" in resp.headers
