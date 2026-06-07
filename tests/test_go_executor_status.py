def test_go_executor_status_disabled(monkeypatch):
    from src.api.services.go_executor_service import get_go_executor_status

    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "false")
    status = get_go_executor_status()

    assert status["enabled"] is False
    assert status["healthy"] is False
    assert status["backend"] == "python"
    assert status["routing_policy"]["mode"] == "auto"


def test_go_executor_status_healthy(monkeypatch):
    from src.api.services.go_executor_service import get_go_executor_status

    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
    monkeypatch.setattr("src.runtime.executor_client.EXECUTOR_ADDR", "localhost:50055")
    monkeypatch.setattr("src.runtime.executor_client.close", lambda: None)
    monkeypatch.setattr(
        "src.runtime.executor_client.health",
        lambda: {"ok": True, "service": "nanocursor-executor", "version": "0.1.0"},
    )

    status = get_go_executor_status()

    assert status["enabled"] is True
    assert status["healthy"] is True
    assert status["backend"] == "go"
    assert status["service"] == "nanocursor-executor"


def test_go_executor_status_failure_falls_back_to_python(monkeypatch):
    from src.api.services.go_executor_service import get_go_executor_status

    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_FALLBACK", "true")
    monkeypatch.setattr("src.runtime.executor_client.EXECUTOR_ADDR", "localhost:50055")
    monkeypatch.setattr("src.runtime.executor_client.close", lambda: None)

    def fail_health():
        raise RuntimeError("connection refused")

    monkeypatch.setattr("src.runtime.executor_client.health", fail_health)

    status = get_go_executor_status()

    assert status["enabled"] is True
    assert status["fallback_enabled"] is True
    assert status["healthy"] is False
    assert status["backend"] == "python"
    assert "connection refused" in status["error"]


def test_go_executor_status_route_disabled(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.server import app

    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "false")
    resp = TestClient(app).get("/api/runtime/executor/status")

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
