from fastapi.testclient import TestClient


class _HealthyFileToolsClient:
    def __init__(self, workspace: str, server_addr: str | None = None):
        self.workspace = workspace
        self.server_addr = server_addr
        self.closed = False

    def health_sync(self, timeout_seconds: float = 1.0):
        return {"ok": True, "service": "nanocursor-filetools", "version": "0.1.0"}

    def close(self):
        self.closed = True


class _FailingFileToolsClient:
    def __init__(self, workspace: str, server_addr: str | None = None):
        pass

    def health_sync(self, timeout_seconds: float = 1.0):
        raise RuntimeError("connection refused")

    def close(self):
        pass


def test_go_filetools_status_disabled(monkeypatch):
    from src.api.services.go_filetools_service import get_go_filetools_status

    monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "false")
    status = get_go_filetools_status()
    assert status["enabled"] is False
    assert status["healthy"] is False
    assert status["backend"] == "python"
    assert status["error"] is None


def test_go_filetools_status_healthy(monkeypatch):
    from src.api.services.go_filetools_service import get_go_filetools_status

    monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ADDR", "localhost:50054")
    monkeypatch.setattr("src.tools.filetools_client.FileToolsClient", _HealthyFileToolsClient)

    status = get_go_filetools_status()
    assert status["enabled"] is True
    assert status["healthy"] is True
    assert status["backend"] == "go"
    assert status["service"] == "nanocursor-filetools"
    assert status["version"] == "0.1.0"


def test_go_filetools_status_failure_falls_back_to_python(monkeypatch):
    from src.api.services.go_filetools_service import get_go_filetools_status

    monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "true")
    monkeypatch.setattr("src.tools.filetools_client.FileToolsClient", _FailingFileToolsClient)

    status = get_go_filetools_status()
    assert status["enabled"] is True
    assert status["healthy"] is False
    assert status["backend"] == "python"
    assert "connection refused" in status["error"]


def test_go_filetools_status_route_disabled(monkeypatch):
    from src.api.server import app

    monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "false")
    client = TestClient(app)
    resp = client.get("/api/runtime/filetools/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["backend"] == "python"

