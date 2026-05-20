"""MCP server config details and static validation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.infra.path_guard import safe_slug
from src.api.services.capability_service import MCP_TEMPLATES


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _mcp_config_candidates(workspace: Path) -> list[Path]:
    return [
        workspace / ".mcp.json",
        workspace / ".cursor" / "mcp.json",
        workspace / ".nanocursor" / "mcp.json",
    ]


def _writable_mcp_config_path(workspace: Path) -> Path:
    return workspace / ".nanocursor" / "mcp.json"


def _server_slug(server_id: str) -> str:
    raw = str(server_id or "").strip()
    raw = raw[4:] if raw.startswith("mcp.") else raw
    if not raw:
        raise ValueError("MCP server 名称不能为空。")
    return safe_slug(raw, max_length=60)


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data.get("servers") if isinstance(data.get("servers"), dict) else {}
    data["mcpServers"] = servers
    data.pop("servers", None)
    return data


def _scan_last_used_run(workspace: Path, server_id: str) -> str | None:
    """Find the most recent run that used a given MCP server."""
    runs_root = workspace / ".nanocursor" / "runs"
    if not runs_root.exists():
        return None
    # sort by mtime descending, limit to 20
    run_dirs = sorted(
        [d for d in runs_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[:20]
    for run_dir in run_dirs:
        events_file = run_dir / "events.jsonl"
        if not events_file.exists():
            continue
        try:
            for line in events_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") != "tool_call_finished":
                    continue
                trace = event.get("payload", {}).get("capability_trace", {})
                if isinstance(trace, dict) and trace.get("capability_id") == server_id:
                    return run_dir.name
                if isinstance(trace, str) and trace == server_id:
                    return run_dir.name
        except (OSError, json.JSONDecodeError):
            continue
    return None


def upsert_mcp_server_config(
    server_id: str,
    command: str,
    args: list[str] | None = None,
    env_keys: list[str] | None = None,
    workspace_dir: str | None = None,
    *,
    enabled: bool = True,
    ignored_env_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update a workspace-local MCP server config."""
    workspace = _workspace(workspace_dir)
    slug = _server_slug(server_id)
    command = str(command or "").strip()
    if not command:
        raise ValueError("MCP server command 不能为空。")

    normalized_args = [str(item).strip() for item in (args or []) if str(item).strip()]
    normalized_env_keys = [str(item).strip() for item in (env_keys or []) if str(item).strip()]
    normalized_ignored_env_keys = [
        str(item).strip() for item in (ignored_env_keys or []) if str(item).strip()
    ]

    path = _writable_mcp_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config(path)
    data.setdefault("mcpServers", {})
    data["mcpServers"][slug] = {
        "command": command,
        "args": normalized_args,
        "env": {key: f"${{{key}}}" for key in normalized_env_keys},
        "enabled": bool(enabled),
        "ignored_env_keys": normalized_ignored_env_keys,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "id": f"mcp.{slug}",
        "name": f"{slug} MCP",
        "status": "configured",
        "source": str(path.relative_to(workspace)),
        "command": command,
        "args": normalized_args,
        "env_keys": normalized_env_keys,
        "enabled": bool(enabled),
        "ignored_env_keys": normalized_ignored_env_keys,
    }


def list_mcp_servers(workspace_dir: str | None = None) -> dict[str, Any]:
    """Read MCP config files and return detailed server info."""
    workspace = _workspace(workspace_dir)
    candidates = _mcp_config_candidates(workspace)

    config_paths: list[str] = []
    configured_ids: set[str] = set()
    servers: list[dict[str, Any]] = []

    for path in candidates:
        if not path.exists():
            continue
        try:
            rel = str(path.relative_to(workspace))
        except ValueError:
            rel = str(path)
        config_paths.append(rel)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        raw_servers = data.get("mcpServers") or data.get("servers") or {}
        if not isinstance(raw_servers, dict):
            continue

        for name, raw in raw_servers.items():
            if not isinstance(raw, dict):
                continue
            server_id = f"mcp.{name}"
            configured_ids.add(server_id)
            command = raw.get("command", "")
            args = raw.get("args", []) if isinstance(raw.get("args"), list) else []
            env = raw.get("env", {}) if isinstance(raw.get("env"), dict) else {}
            env_keys = list(env.keys()) if env else []
            enabled = bool(raw.get("enabled", True))
            ignored_env_keys = raw.get("ignored_env_keys", [])
            if not isinstance(ignored_env_keys, list):
                ignored_env_keys = []
            status = "configured" if command else "missing"
            servers.append({
                "id": server_id,
                "name": f"{name} MCP",
                "status": status,
                "source": rel,
                "command": command,
                "args": args,
                "env_keys": env_keys,
                "enabled": enabled,
                "ignored_env_keys": ignored_env_keys,
                "setup_hint": f"已从 {rel} 读取 {name} server 配置。" if command else f"在 {rel} 中找到 {name} 但未声明 command。",
                "last_used_run_id": None,
            })

    # Add template servers for any that weren't found in real config
    for template in MCP_TEMPLATES:
        if template["id"] not in configured_ids:
            servers.append({
                "id": template["id"],
                "name": template["name"],
                "status": "planned",
                "source": "",
                "command": "",
                "args": [],
                "env_keys": [],
                "enabled": False,
                "ignored_env_keys": [],
                "setup_hint": template.get("setup_hint", ""),
                "last_used_run_id": None,
            })

    # Scan run history for last_used_run_id
    for server in servers:
        if server["status"] in ("configured",):
            server["last_used_run_id"] = _scan_last_used_run(workspace, server["id"])

    servers.sort(key=lambda s: (
        {"ready": 0, "configured": 1, "planned": 2, "missing": 3}.get(s["status"], 4),
        s["name"],
    ))

    summary = {
        "total": len(servers),
        "configured": sum(1 for s in servers if s["status"] == "configured"),
        "planned": sum(1 for s in servers if s["status"] == "planned"),
        "missing": sum(1 for s in servers if s["status"] == "missing"),
    }

    return {
        "workspace_dir": str(workspace),
        "config_paths": config_paths,
        "servers": servers,
        "summary": summary,
    }


def validate_mcp_config(
    server_id: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Run static validation checks on MCP configuration."""
    config = list_mcp_servers(workspace_dir)
    servers = config["servers"]
    config_paths = config["config_paths"]

    target = [s for s in servers if server_id is None or s["id"] == server_id]
    if server_id and not target:
        return {
            "status": "failed",
            "servers": {},
            "checks": [{
                "id": "server_not_found",
                "label": "查找 server",
                "status": "failed",
                "detail": f"未找到 server: {server_id}",
            }],
        }

    all_checks: list[dict[str, Any]] = []
    server_results: dict[str, Any] = {}

    for server in target:
        checks: list[dict[str, Any]] = []

        # config_exists
        if server["source"]:
            checks.append({
                "id": "config_exists",
                "label": "检测配置文件",
                "status": "passed",
                "detail": f"已找到 {server['source']}",
            })
        else:
            checks.append({
                "id": "config_exists",
                "label": "检测配置文件",
                "status": "warning",
                "detail": "未找到 MCP 配置文件，请在工作区添加 .mcp.json。",
            })

        # command_exists
        if server["command"]:
            checks.append({
                "id": "command_exists",
                "label": "检测启动命令",
                "status": "passed",
                "detail": f"command: {server['command']}",
            })
        else:
            checks.append({
                "id": "command_exists",
                "label": "检测启动命令",
                "status": "warning" if server["source"] else "planned",
                "detail": f"{server['name']} 未声明 command。",
            })

        # env_keys_available
        for key in server["env_keys"]:
            if os.environ.get(key):
                checks.append({
                    "id": f"env_{key}",
                    "label": f"环境变量 {key}",
                    "status": "passed",
                    "detail": f"{key} 已设置。",
                })
            else:
                checks.append({
                    "id": f"env_{key}",
                    "label": f"环境变量 {key}",
                    "status": "warning",
                    "detail": f"{key} 未在环境变量中设置。",
                })

        # Determine overall status
        statuses = [c["status"] for c in checks]
        if any(s == "failed" for s in statuses):
            overall = "failed"
        elif any(s == "warning" for s in statuses):
            overall = "warning"
        elif all(s == "passed" for s in statuses):
            overall = "passed"
        else:
            overall = "planned"

        server_results[server["id"]] = {"status": overall, "checks": checks}
        all_checks.extend(checks)

    overall_status = "passed"
    for s in server_results.values():
        if s["status"] == "failed":
            overall_status = "failed"
            break
        if s["status"] == "warning":
            overall_status = "warning"

    return {
        "status": overall_status,
        "servers": server_results,
        "checks": all_checks,
    }
