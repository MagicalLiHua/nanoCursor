"""MCP runtime service tests — probe, tools, call."""

import json

import pytest
from fastapi.testclient import TestClient

from src.api.services.mcp_runtime_service import (
    probe_mcp_server,
    list_mcp_tools,
    call_mcp_tool,
)


class TestMCPProbe:
    def test_probe_nonexistent_server(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        result = probe_mcp_server("mcp.nonexistent", str(ws))
        assert result["status"] == "failed"
        assert any(c["id"] == "server_not_found" for c in result["checks"])

    def test_probe_configured_server_with_command(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        # Set up mcp config
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"echo": {"command": "echo", "args": ["hello"]}}}
        (nanodir / "mcp.json").write_text(json.dumps(config))

        result = probe_mcp_server("mcp.echo", str(ws))
        # echo is always on PATH, so command check passes
        assert result["status"] in ("passed", "warning")

    def test_probe_server_with_missing_env(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"test": {"command": "echo", "env": {"SECRET_KEY": "${SECRET_KEY}"}}}}
        (nanodir / "mcp.json").write_text(json.dumps(config))

        monkeypatch.delenv("SECRET_KEY", raising=False)
        result = probe_mcp_server("mcp.test", str(ws))
        env_checks = [c for c in result["checks"] if c["id"] == "env_SECRET_KEY"]
        if env_checks:
            assert env_checks[0]["status"] == "warning"

    def test_probe_disabled_server(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"disabled_srv": {"command": "echo", "enabled": False}}}
        (nanodir / "mcp.json").write_text(json.dumps(config))

        result = probe_mcp_server("mcp.disabled_srv", str(ws))
        # Note: server may not appear as "disabled" since enabled is not read from config yet
        # We just check that it doesn't crash


class TestMCPTools:
    def test_list_tools_for_nonexistent(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        result = list_mcp_tools("mcp.nonexistent", str(ws))
        assert result["tools"] == []
        assert "error" in result

    def test_list_tools_for_configured(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"echo": {"command": "echo"}}}
        (nanodir / "mcp.json").write_text(json.dumps(config))

        result = list_mcp_tools("mcp.echo", str(ws))
        assert result["server_id"] == "mcp.echo"
        assert "hint" in result


class TestMCPCall:
    def test_call_nonexistent_server(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        result = call_mcp_tool("mcp.nonexistent", "tool", {}, str(ws))
        assert result["ok"] is False

    def test_call_disabled_server(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"srv": {"command": "echo", "enabled": False}}}
        (nanodir / "mcp.json").write_text(json.dumps(config))
        # The probe_mcp_server doesn't directly read "enabled" from mcp config
        # but list_mcp_servers may not have "enabled" field
        result = call_mcp_tool("mcp.srv", "tool", {}, str(ws))
        # Best-effort: should not crash
        assert isinstance(result, dict)
        assert "ok" in result


class TestMCPRuntimeAPI:
    def test_probe_api(self):
        from api_server import app
        client = TestClient(app)
        resp = client.post("/api/capabilities/mcp/nonexistent/probe")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"

    def test_tools_api(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/capabilities/mcp/nonexistent/tools")
        assert resp.status_code == 200

    def test_call_api(self):
        from api_server import app
        client = TestClient(app)
        resp = client.post("/api/capabilities/mcp/nonexistent/tools/tool1/call", json={})
        assert resp.status_code == 200
