from fastapi.testclient import TestClient

from src.api.server import app
from src.api.services.go_mcp_gateway_service import get_go_mcp_gateway_status


def test_go_mcp_gateway_status_disabled(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_GO_MCP_GATEWAY_ENABLED", raising=False)

    status = get_go_mcp_gateway_status()

    assert status["enabled"] is False
    assert status["backend"] == "python"
    assert status["healthy"] is False


def test_go_mcp_gateway_status_healthy(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_MCP_GATEWAY_ENABLED", "1")
    monkeypatch.setattr("src.runtime.mcp_client.MCP_ADDR", "localhost:50056")
    monkeypatch.setattr("src.runtime.mcp_client.close", lambda: None)
    monkeypatch.setattr(
        "src.runtime.mcp_client.health",
        lambda: {"ok": True, "service": "nanocursor-mcp", "version": "0.1.0"},
    )

    status = get_go_mcp_gateway_status()

    assert status["enabled"] is True
    assert status["backend"] == "go"
    assert status["healthy"] is True
    assert status["service"] == "nanocursor-mcp"


def test_go_mcp_gateway_status_failure(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_MCP_GATEWAY_ENABLED", "1")
    monkeypatch.setattr("src.runtime.mcp_client.MCP_ADDR", "localhost:50056")
    monkeypatch.setattr("src.runtime.mcp_client.close", lambda: None)

    def fail_health():
        raise RuntimeError("connection refused")

    monkeypatch.setattr("src.runtime.mcp_client.health", fail_health)

    status = get_go_mcp_gateway_status()

    assert status["enabled"] is True
    assert status["healthy"] is False
    assert "connection refused" in status["error"]


def test_go_mcp_gateway_status_route_disabled(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_GO_MCP_GATEWAY_ENABLED", raising=False)

    response = TestClient(app).get("/api/runtime/mcp-gateway/status")

    assert response.status_code == 200
    assert response.json()["backend"] == "python"


def test_mcp_probe_does_not_silently_fallback_when_gateway_required(monkeypatch, tmp_path):
    from src.api.services import mcp_runtime_service as service

    monkeypatch.setenv("NANOCURSOR_GO_MCP_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_GO_MCP_GATEWAY_FALLBACK", "false")
    monkeypatch.setattr(service, "_go_mcp_probe", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "_mcp_server_config",
        lambda server_id, workspace_dir=None: {"id": server_id, "command": "fake-mcp", "args": []},
    )

    result = service.probe_mcp_server("mcp.fake", str(tmp_path))

    assert result["status"] == "failed"
    assert result["checks"][0]["id"] == "go_mcp_gateway"
