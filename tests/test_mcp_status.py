"""D7 MCP status cache tests."""

from src.api.services.mcp_status_service import (
    get_mcp_server_status, get_mcp_status, record_mcp_usage,
    set_mcp_enabled, update_mcp_status,
)


def test_get_mcp_status_defaults_empty(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    status = get_mcp_status(str(workspace))
    assert "servers" in status
    assert status["servers"] == {}


def test_update_mcp_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    updated = update_mcp_status("mcp.github", {"status": "configured"}, str(workspace))
    assert updated["status"] == "configured"
    assert updated["last_validated_at"] is not None


def test_set_mcp_enabled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = set_mcp_enabled("mcp.github", True, str(workspace))
    assert result["enabled"] is True
    result = set_mcp_enabled("mcp.github", False, str(workspace))
    assert result["enabled"] is False


def test_record_mcp_usage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record_mcp_usage("mcp.github", "run-123", str(workspace))
    status = get_mcp_server_status("mcp.github", str(workspace))
    assert status["last_used_run_id"] == "run-123"


def test_get_mcp_server_status_unknown(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    status = get_mcp_server_status("mcp.unknown", str(workspace))
    assert status["status"] == "unknown"


def test_mcp_status_persists(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    update_mcp_status("mcp.github", {"status": "configured"}, str(workspace))

    # Re-read
    status2 = get_mcp_status(str(workspace))
    assert status2["servers"]["mcp.github"]["status"] == "configured"
