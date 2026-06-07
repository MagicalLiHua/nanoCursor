"""
Compatibility shim -- prefer src.runtime.mcp_client for new code.
This module delegates to mcp_client when available, falls back to HTTP.
"""

from __future__ import annotations

from typing import Any

from src.runtime.go_runtime_client import _get_json, _post_json


def list_mcp_presets() -> list[dict[str, Any]]:
    payload = _get_json("/v1/mcp/presets")
    presets = payload.get("presets") if isinstance(payload, dict) else []
    return presets if isinstance(presets, list) else []


def probe_mcp_server(
    *,
    server_id: str,
    workspace_dir: str,
    command: str,
    args: list[str] | None = None,
    env_keys: list[str] | None = None,
    env: dict[str, str] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "server_id": server_id,
        "workspace_dir": workspace_dir,
        "command": command,
        "args": args or [],
        "env_keys": env_keys or [],
        "env": env or {},
    }
    if enabled is not None:
        payload["enabled"] = enabled
    return _post_json("/v1/mcp/servers/probe", payload)


def list_mcp_servers() -> list[dict[str, Any]]:
    payload = _get_json("/v1/mcp/servers")
    servers = payload.get("servers") if isinstance(payload, dict) else []
    return servers if isinstance(servers, list) else []


def list_mcp_tools(server_id: str) -> dict[str, Any]:
    return _get_json(f"/v1/mcp/servers/{server_id}/tools")


def call_mcp_tool(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    run_id: str = "",
    workspace_dir: str = "",
    permission_level: str = "",
    requires_approval: bool = False,
    approval_id: str = "",
    approval_token: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "server_id": server_id,
        "tool_name": tool_name,
        "arguments": arguments or {},
    }
    if run_id:
        payload["run_id"] = run_id
    if workspace_dir:
        payload["workspace_dir"] = workspace_dir
    if permission_level or requires_approval or approval_id or approval_token:
        payload["policy"] = {
            "permission_level": permission_level,
            "requires_approval": requires_approval,
            "approval_id": approval_id,
            "approval_token": approval_token,
        }
    return _post_json("/v1/mcp/tools/call", payload)
