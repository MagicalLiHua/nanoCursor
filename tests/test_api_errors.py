"""API error response tests — verify unified error.code/message/hint/request_id shape."""

from fastapi.testclient import TestClient


def _client():
    from api_server import app
    return TestClient(app, raise_server_exceptions=False)


def test_mcp_server_empty_command_returns_400():
    """Empty MCP command should return 400 with error code."""
    client = _client()
    resp = client.post("/api/capabilities/mcp/servers", json={
        "server_id": "test",
        "command": "",
        "args": [],
        "env_keys": [],
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_nonexistent_run_returns_404_with_error_code():
    """Non-existent run should return 404 with recognizable error structure."""
    client = _client()
    resp = client.get("/api/runs/nonexistent_run_xyz_12345")
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "resource_not_found"
    assert error["message"] == "Run 不存在"


def test_invalid_workspace_path_returns_400():
    """A relative (non-absolute) workspace path should return 400."""
    client = _client()
    resp = client.post("/api/workspaces", json={"dir": "relative/path"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_error_response_has_request_id():
    """Every error response includes a request_id for traceability."""
    client = _client()
    resp = client.get("/api/runs/nonexistent_run_xyz")
    assert resp.status_code == 404
    assert resp.json()["error"]["request_id"]


def test_http_200_routes_return_200():
    """Smoke: core routes return 2xx."""
    client = _client()
    assert client.get("/health").status_code == 200
    assert client.get("/api/system/version").status_code == 200
    assert client.get("/api/capabilities").status_code == 200
    assert client.get("/api/metrics").status_code == 200


def test_open_workspace_with_absolute_path(tmp_path):
    """Opening a valid absolute path workspace should succeed."""
    client = _client()
    ws = tmp_path / "test_ws"
    ws.mkdir()
    resp = client.post("/api/workspaces/open", json={"path": str(ws)})
    assert resp.status_code == 200
    data = resp.json()
    assert "path" in data
