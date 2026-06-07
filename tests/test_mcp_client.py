"""Tests for MCP gRPC client — requires go-mcp running on localhost:50056."""

import os
import pytest


def mcp_available():
    try:
        from src.runtime.mcp_client import health
        result = health()
        return result.get("ok", False)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not mcp_available(), reason="go-mcp not running")


class TestMCPHealth:
    def test_health(self):
        from src.runtime.mcp_client import health
        result = health()
        assert result["ok"] is True
        assert result["service"] == "nanocursor-mcp"


class TestMCPPresets:
    def test_list_presets(self):
        from src.runtime.mcp_client import list_presets
        presets = list_presets()
        assert len(presets) == 5
        ids = {p["id"] for p in presets}
        assert "filesystem" in ids
        assert "github" in ids


class TestMCPServerProbe:
    def test_probe_echo(self):
        from src.runtime.mcp_client import probe_server
        result = probe_server("test.echo", "echo", args=["hello"])
        assert result["ok"] is True
        assert result["status"] == "passed"

    def test_probe_missing_command(self):
        from src.runtime.mcp_client import probe_server
        result = probe_server("bad.server", "nonexistent_command_xyz")
        assert result["ok"] is False

    def test_probe_appears_in_server_list(self):
        from src.runtime.mcp_client import probe_server, list_servers
        probe_server("test.probed", "echo")
        servers = list_servers()
        ids = {s["id"] for s in servers}
        assert "test.probed" in ids


class TestMCPCallUnregistered:
    def test_call_unregistered_server(self):
        from src.runtime.mcp_client import call_mcp_tool
        result = call_mcp_tool("unregistered.server", "test_tool")
        assert result["ok"] is False
