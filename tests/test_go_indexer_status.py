class _HealthyIndexerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def health_sync(self, timeout_seconds=1.0):
        return {"ok": True, "service": "nanocursor-indexer", "version": "0.1.0", "indexed_files": 12}

    def close(self):
        pass


class _FailingIndexerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def health_sync(self, timeout_seconds=1.0):
        raise RuntimeError("connection refused")

    def close(self):
        pass


def test_go_indexer_status_disabled(monkeypatch):
    from src.api.services.go_indexer_service import get_go_indexer_status

    monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "false")
    status = get_go_indexer_status()

    assert status["enabled"] is False
    assert status["healthy"] is False
    assert status["backend"] == "python"


def test_go_indexer_status_healthy(monkeypatch):
    from src.api.services.go_indexer_service import get_go_indexer_status

    monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "true")
    monkeypatch.setattr("src.indexer.indexer_grpc.ProjectIndexClient", _HealthyIndexerClient)
    status = get_go_indexer_status()

    assert status["enabled"] is True
    assert status["healthy"] is True
    assert status["backend"] == "go"
    assert status["service"] == "nanocursor-indexer"
    assert status["indexed_files"] == 12


def test_go_indexer_status_failure_falls_back_to_python(monkeypatch):
    from src.api.services.go_indexer_service import get_go_indexer_status

    monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_GO_INDEXER_FALLBACK", "true")
    monkeypatch.setattr("src.indexer.indexer_grpc.ProjectIndexClient", _FailingIndexerClient)
    status = get_go_indexer_status()

    assert status["enabled"] is True
    assert status["fallback_enabled"] is True
    assert status["healthy"] is False
    assert status["backend"] == "python"
    assert "connection refused" in status["error"]


def test_go_indexer_status_route_disabled(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.server import app

    monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "false")
    resp = TestClient(app).get("/api/runtime/indexer/status")

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
