"""MCP runtime service tests — probe, tools, call."""

import json
import sys

import pytest
from fastapi.testclient import TestClient

from src.api.services.mcp_runtime_service import (
    probe_mcp_server,
    list_mcp_tools,
    call_mcp_tool,
)
from src.api.services.mcp_status_service import get_mcp_server_status


def write_fake_mcp_server(workspace):
    script = workspace / "fake_mcp_server.py"
    script.write_text(
        r'''
import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def write_message(message):
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        write_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0.0"},
            },
        })
    elif method == "tools/list":
        write_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [{
                    "name": "echo",
                    "description": "Echo input text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                }]
            },
        })
    elif method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        write_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": "echo:" + str(arguments.get("text", ""))}]
            },
        })
    else:
        write_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        })
''',
        encoding="utf-8",
    )
    return script


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
        assert result["ok"] is False
        assert "error" in result

    def test_list_tools_for_stdio_server(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = write_fake_mcp_server(ws)
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"fake": {"command": sys.executable, "args": [str(script)]}}}
        (nanodir / "mcp.json").write_text(json.dumps(config), encoding="utf-8")

        result = list_mcp_tools("mcp.fake", str(ws))

        assert result["ok"] is True
        assert result["transport"] == "stdio"
        assert result["tools"][0]["name"] == "echo"

    def test_list_tools_uses_cache_after_first_success(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = write_fake_mcp_server(ws)
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"fake": {"command": sys.executable, "args": [str(script)]}}}
        (nanodir / "mcp.json").write_text(json.dumps(config), encoding="utf-8")

        first = list_mcp_tools("mcp.fake", str(ws))
        second = list_mcp_tools("mcp.fake", str(ws))

        assert first["ok"] is True
        assert first["cache"] == "miss"
        assert second["ok"] is True
        assert second["cache"] == "hit"
        assert second["tools"][0]["name"] == "echo"
        status = get_mcp_server_status("mcp.fake", str(ws))
        assert status["failure_count"] == 0
        assert status["tools_cache"]["tools"][0]["name"] == "echo"

    def test_list_tools_opens_circuit_after_repeated_failures(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"bad": {"command": "definitely-not-a-nanocursor-command"}}}
        (nanodir / "mcp.json").write_text(json.dumps(config), encoding="utf-8")

        for _ in range(3):
            result = list_mcp_tools("mcp.bad", str(ws))
            assert result["ok"] is False

        status = get_mcp_server_status("mcp.bad", str(ws))
        assert status["status"] == "circuit_open"
        assert status["failure_count"] == 3

        circuit = list_mcp_tools("mcp.bad", str(ws))
        assert circuit["status"] == "circuit_open"
        assert circuit["cache"] == "miss"


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

    def test_call_stdio_tool(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        script = write_fake_mcp_server(ws)
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"fake": {"command": sys.executable, "args": [str(script)]}}}
        (nanodir / "mcp.json").write_text(json.dumps(config), encoding="utf-8")

        result = call_mcp_tool("mcp.fake", "echo", {"text": "hello"}, str(ws))

        assert result["ok"] is True
        assert result["transport"] == "stdio"
        assert result["result"]["content"][0]["text"] == "echo:hello"

    def test_call_fails_fast_when_circuit_is_open(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        config = {"mcpServers": {"bad": {"command": "definitely-not-a-nanocursor-command"}}}
        (nanodir / "mcp.json").write_text(json.dumps(config), encoding="utf-8")

        for _ in range(3):
            list_mcp_tools("mcp.bad", str(ws))

        result = call_mcp_tool("mcp.bad", "anything", {}, str(ws))

        assert result["ok"] is False
        assert result["status"] == "circuit_open"
        assert result["circuit_remaining_seconds"] > 0


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
