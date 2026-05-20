"""MCP server status cache and validation tracking."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _status_path(workspace: Path) -> Path:
    nc = workspace / ".nanocursor"
    nc.mkdir(parents=True, exist_ok=True)
    return nc / "mcp_status.json"


def get_mcp_status(workspace_dir: str | None = None) -> dict[str, Any]:
    """Read MCP status cache."""
    workspace = _workspace(workspace_dir)
    sp = _status_path(workspace)
    if not sp.exists():
        return {"servers": {}}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"servers": {}}


def update_mcp_status(server_id: str, updates: dict[str, Any], workspace_dir: str | None = None) -> dict[str, Any]:
    """Update status for a specific MCP server."""
    workspace = _workspace(workspace_dir)
    sp = _status_path(workspace)
    status = get_mcp_status(str(workspace))
    servers = status.setdefault("servers", {})

    current = servers.get(server_id, {})
    current.update(updates)
    current["last_validated_at"] = time.time()
    servers[server_id] = current

    sp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def set_mcp_enabled(server_id: str, enabled: bool, workspace_dir: str | None = None) -> dict[str, Any]:
    """Enable or disable an MCP server."""
    return update_mcp_status(server_id, {"enabled": enabled}, workspace_dir)


def record_mcp_usage(server_id: str, run_id: str, workspace_dir: str | None = None) -> None:
    """Record that a run used an MCP server."""
    update_mcp_status(server_id, {"last_used_run_id": run_id}, workspace_dir)


def get_mcp_server_status(server_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Get status for a single MCP server."""
    all_status = get_mcp_status(workspace_dir)
    return all_status.get("servers", {}).get(server_id, {
        "server_id": server_id,
        "status": "unknown",
        "last_validated_at": None,
        "last_used_run_id": None,
    })
