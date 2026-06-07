"""MCP registry, catalog, and governed route tests."""

from __future__ import annotations

import json
import sys

from fastapi.testclient import TestClient

from src.api.services.mcp_tool_catalog_service import classify_mcp_tool, preview_mcp_tool_call
from tests.test_mcp_runtime_service import write_fake_mcp_server


def test_mcp_tool_catalog_classifies_read_write_and_unknown():
    assert classify_mcp_tool("mcp.fake", {"name": "read_file"})["permission_level"] == "mcp_read"
    write_tool = classify_mcp_tool("mcp.fake", {"name": "write_file"})
    assert write_tool["permission_level"] == "mcp_write"
    assert write_tool["requires_approval"] is True
    unknown = classify_mcp_tool("mcp.fake", {"name": "echo"})
    assert unknown["permission_level"] == "external_risky"
    assert unknown["requires_approval"] is True


def test_mcp_tool_catalog_preserves_degraded_fallback_summary():
    from src.api.services.mcp_tool_catalog_service import build_mcp_tool_catalog

    catalog = build_mcp_tool_catalog([
        {
            "server_id": "mcp.docs",
            "status": "degraded",
            "ok": False,
            "tools": [{"name": "read_docs", "description": "Read docs"}],
            "fallback": {"used": True, "strategy": "stale_tool_catalog"},
        }
    ])

    assert catalog["summary"]["degraded_servers"] == 1
    assert catalog["summary"]["fallback_servers"] == 1
    assert catalog["servers"][0]["fallback"]["strategy"] == "stale_tool_catalog"
    assert catalog["tools"][0]["permission_level"] == "mcp_read"


def test_mcp_tool_preview_uses_action_policy(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    read = preview_mcp_tool_call("mcp.fake", "read_echo", {"text": "hi"}, workspace_dir=str(ws))
    assert read["allowed"] is True
    assert read["requires_approval"] is False
    assert read["permission_level"] == "mcp_read"

    write = preview_mcp_tool_call("mcp.fake", "write_note", {"text": "hi"}, workspace_dir=str(ws))
    assert write["allowed"] is True
    assert write["requires_approval"] is True
    assert write["permission_level"] == "mcp_write"


def test_formal_mcp_routes_manage_servers_and_catalog(tmp_path, monkeypatch):
    from src.api.server import app
    import src.infra.config as config_module

    ws = tmp_path / "workspace"
    ws.mkdir()
    script = write_fake_mcp_server(ws)
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(ws))
    client = TestClient(app)

    created = client.post("/api/mcp/servers", json={
        "server_id": "fake",
        "command": sys.executable,
        "args": [str(script)],
        "enabled": True,
    })
    assert created.status_code == 200
    assert created.json()["server"]["id"] == "mcp.fake"

    listed = client.get("/api/mcp/servers")
    assert listed.status_code == 200
    assert any(server["id"] == "mcp.fake" for server in listed.json()["servers"])

    tools = client.get("/api/mcp/tools?refresh=true")
    assert tools.status_code == 200
    data = tools.json()
    names = {tool["name"]: tool for tool in data["catalog"]}
    assert names["read_echo"]["permission_level"] == "mcp_read"
    assert names["write_note"]["permission_level"] == "mcp_write"

    disabled = client.patch("/api/mcp/servers/mcp.fake", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    raw_config = json.loads((ws / ".nanocursor" / "mcp.json").read_text(encoding="utf-8"))
    assert raw_config["mcpServers"]["fake"]["enabled"] is False

    preview = client.post("/api/mcp/tools/preview", json={
        "server_id": "mcp.fake",
        "tool_name": "write_note",
        "arguments": {"text": "hello"},
    })
    assert preview.status_code == 200
    assert preview.json()["requires_approval"] is True
    assert preview.json()["permission_level"] == "mcp_write"


def test_formal_mcp_call_route_goes_through_action_policy(tmp_path, monkeypatch):
    from src.api.server import app
    import src.infra.config as config_module

    ws = tmp_path / "workspace"
    ws.mkdir()
    script = write_fake_mcp_server(ws)
    nanodir = ws / ".nanocursor"
    nanodir.mkdir()
    (nanodir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"fake": {"command": sys.executable, "args": [str(script)]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(ws))
    client = TestClient(app)

    read = client.post(
        "/api/mcp/servers/mcp.fake/tools/read_echo/call",
        json={"thread_id": "run-mcp-read", "arguments": {"text": "hello"}},
    )
    assert read.status_code == 200
    assert read.json()["result"] == "success"
    assert read.json()["detail"]["permission_level"] == "mcp_read"

    write = client.post(
        "/api/mcp/servers/mcp.fake/tools/write_note/call",
        json={"thread_id": "run-mcp-write", "arguments": {"text": "hello"}},
    )
    assert write.status_code == 200
    assert write.json()["result"] == "pending"
    assert write.json()["requires_approval"] is True
    assert write.json()["detail"]["permission_level"] == "mcp_write"
