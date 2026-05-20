"""MCP runtime service — probe, list tools, call tools.

Phase 1: static / semi-dynamic checks (command on PATH, env keys present,
config parseable, server enabled).
Phase 2: real MCP protocol client (future).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.mcp_service import list_mcp_servers


def _mcp_server_config(server_id: str, workspace_dir: str | None = None) -> dict[str, Any] | None:
    """Find a single MCP server by id in the workspace config."""
    data = list_mcp_servers(workspace_dir)
    for s in data.get("servers", []):
        if s["id"] == server_id:
            return s
    return None


def probe_mcp_server(
    server_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Run static / semi-dynamic diagnostics on one MCP server.

    Returns a dict with ``status`` (passed/warning/failed) and ``checks`` list.
    """
    server = _mcp_server_config(server_id, workspace_dir)
    ws = workspace_dir or config_module.WORKSPACE_DIR
    checks: list[dict[str, Any]] = []

    if server is None:
        return {
            "server_id": server_id,
            "status": "failed",
            "checks": [{"id": "server_not_found", "label": "Server 查找",
                        "status": "failed", "detail": f"未找到 MCP server: {server_id}"}],
        }

    # 1. Server enabled
    enabled = server.get("enabled", True)
    if enabled is False:
        checks.append({"id": "enabled", "label": "Server 已启用",
                       "status": "failed", "detail": "该 MCP server 已被禁用。"})
        return {
            "server_id": server_id,
            "status": "failed",
            "checks": checks,
            "server": server,
        }
    checks.append({"id": "enabled", "label": "Server 已启用",
                   "status": "passed", "detail": "已启用。"})

    # 2. Command exists on PATH
    command = server.get("command", "")
    if command:
        found = shutil.which(command) is not None
        checks.append({
            "id": "command_on_path",
            "label": f"命令可执行: {command}",
            "status": "passed" if found else "warning",
            "detail": f"{command} 在 PATH 中。" if found else f"{command} 未在 PATH 中找到。请确认已安装。",
        })
    else:
        checks.append({
            "id": "command_on_path",
            "label": "命令可执行",
            "status": "warning",
            "detail": "未声明 command。",
        })

    # 3. Env keys present
    for key in server.get("env_keys", []):
        present = bool(os.environ.get(key))
        checks.append({
            "id": f"env_{key}",
            "label": f"环境变量: {key}",
            "status": "passed" if present else "warning",
            "detail": f"{key} 已设置。" if present else f"{key} 未设置。请在 .env 中添加。",
        })

    # 4. Config parseable
    try:
        json.dumps(server)
        checks.append({
            "id": "config_valid",
            "label": "配置格式",
            "status": "passed",
            "detail": "配置 JSON 格式正确。",
        })
    except (TypeError, ValueError):
        checks.append({
            "id": "config_valid",
            "label": "配置格式",
            "status": "failed",
            "detail": "配置 JSON 序列化失败。",
        })

    # Overall status
    statuses = [c["status"] for c in checks]
    if any(s == "failed" for s in statuses):
        overall = "failed"
    elif any(s == "warning" for s in statuses):
        overall = "warning"
    else:
        overall = "passed"

    return {
        "server_id": server_id,
        "status": overall,
        "checks": checks,
        "server": server,
    }


def list_mcp_tools(
    server_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """List tools exposed by an MCP server. Currently static placeholder."""
    probe = probe_mcp_server(server_id, workspace_dir)
    if probe["status"] == "failed":
        return {"server_id": server_id, "tools": [], "error": probe["checks"][0]["detail"]}

    server = probe.get("server", {})
    # Phase 1: return empty tool list with a hint
    return {
        "server_id": server_id,
        "command": server.get("command", ""),
        "tools": [],
        "hint": "MCP 协议运行时暂未集成。tools 列表将在 Phase 2 通过 MCP 协议动态获取。",
        "status": probe["status"],
    }


def call_mcp_tool(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Call an MCP tool. Currently static placeholder."""
    probe = probe_mcp_server(server_id, workspace_dir)
    if probe["status"] == "failed":
        return {"server_id": server_id, "tool": tool_name, "ok": False,
                "error": "MCP server 不可用。"}

    disabled_check = next((c for c in probe["checks"] if c["id"] == "enabled" and c["status"] == "failed"), None)
    if disabled_check:
        return {"server_id": server_id, "tool": tool_name, "ok": False,
                "error": "MCP server 已被禁用，无法调用工具。"}

    return {
        "server_id": server_id,
        "tool": tool_name,
        "arguments": arguments or {},
        "ok": False,
        "result": "",
        "hint": "MCP 协议运行时暂未集成。工具调用将在 Phase 2 通过 MCP 协议实现。",
    }
