"""Python adapter tests for Go MCP Gateway endpoints."""

from __future__ import annotations

from src.runtime import go_mcp_gateway_client as client


def test_list_mcp_presets_normalizes_response(monkeypatch):
    monkeypatch.setattr(client, "_get_json", lambda path: {"presets": [{"id": "filesystem"}]})

    assert client.list_mcp_presets() == [{"id": "filesystem"}]


def test_probe_mcp_server_posts_expected_payload(monkeypatch, tmp_path):
    captured = {}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"server_id": payload["server_id"], "status": "passed", "ok": True}

    monkeypatch.setattr(client, "_post_json", fake_post)

    result = client.probe_mcp_server(
        server_id="mcp.echo",
        workspace_dir=str(tmp_path),
        command="echo",
        args=["hello"],
        env_keys=["TOKEN"],
        env={"TOKEN": "x"},
        enabled=True,
    )

    assert result["ok"] is True
    assert captured["path"] == "/v1/mcp/servers/probe"
    assert captured["payload"]["server_id"] == "mcp.echo"
    assert captured["payload"]["args"] == ["hello"]
    assert captured["payload"]["env_keys"] == ["TOKEN"]


def test_call_mcp_tool_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True, "result": {"content": []}}

    monkeypatch.setattr(client, "_post_json", fake_post)

    result = client.call_mcp_tool(
        "mcp.fake",
        "read_echo",
        {"text": "hi"},
        run_id="run-1",
        workspace_dir="/tmp/project",
        permission_level="mcp_read",
    )

    assert result["ok"] is True
    assert captured["path"] == "/v1/mcp/tools/call"
    assert captured["payload"]["server_id"] == "mcp.fake"
    assert captured["payload"]["tool_name"] == "read_echo"
    assert captured["payload"]["arguments"] == {"text": "hi"}
    assert captured["payload"]["run_id"] == "run-1"
    assert captured["payload"]["workspace_dir"] == "/tmp/project"
    assert captured["payload"]["policy"]["permission_level"] == "mcp_read"
