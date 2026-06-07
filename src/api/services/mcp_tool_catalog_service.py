"""MCP tool catalog and permission preview helpers."""

from __future__ import annotations

from typing import Any

from src.runtime.action_policy import ActionKind, check_action, classify_mcp_permission


def classify_mcp_tool(
    server_id: str,
    tool: dict[str, Any] | str,
    *,
    explicit_permission: str = "",
) -> dict[str, Any]:
    """Return the normalized catalog entry for one MCP tool."""
    if isinstance(tool, str):
        raw: dict[str, Any] = {"name": tool}
    else:
        raw = tool if isinstance(tool, dict) else {}
    name = str(raw.get("name") or raw.get("tool") or "").strip()
    payload = {
        "server_id": server_id,
        "tool_name": name,
        "permission_level": explicit_permission or raw.get("permission") or raw.get("permission_level") or "",
    }
    permission_level = classify_mcp_permission(f"{server_id}/{name}", payload)
    requires_approval = permission_level in {"mcp_write", "external_risky"}
    risk = "low" if permission_level == "mcp_read" else "high"
    return {
        "server_id": server_id,
        "name": name,
        "description": str(raw.get("description") or ""),
        "input_schema": raw.get("inputSchema") or raw.get("input_schema") or {},
        "permission_level": permission_level,
        "requires_approval": requires_approval,
        "risk": risk,
        "source": "mcp",
        "raw": raw,
    }


def build_mcp_tool_catalog(
    server_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a flat permission-aware MCP tool catalog from server tool lists."""
    catalog: list[dict[str, Any]] = []
    servers: list[dict[str, Any]] = []
    for item in server_tools:
        server_id = str(item.get("server_id") or "")
        tools = item.get("tools") if isinstance(item.get("tools"), list) else []
        entries = [classify_mcp_tool(server_id, tool) for tool in tools]
        catalog.extend(entries)
        servers.append({
            "server_id": server_id,
            "status": item.get("status", "unknown"),
            "ok": bool(item.get("ok", False)),
            "tool_count": len(entries),
            "error": item.get("error", ""),
            "cache": item.get("cache", ""),
            "fallback": item.get("fallback") if isinstance(item.get("fallback"), dict) else {},
        })

    summary = {
        "servers": len(servers),
        "tools": len(catalog),
        "read_tools": sum(1 for item in catalog if item["permission_level"] == "mcp_read"),
        "write_tools": sum(1 for item in catalog if item["permission_level"] == "mcp_write"),
        "approval_required": sum(1 for item in catalog if item["requires_approval"]),
        "degraded_servers": sum(1 for item in servers if item.get("status") == "degraded"),
        "fallback_servers": sum(1 for item in servers if item.get("fallback", {}).get("used")),
    }
    return {
        "tools": catalog,
        "servers": servers,
        "summary": summary,
    }


def preview_mcp_tool_call(
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    workspace_dir: str = "",
    thread_id: str = "",
    permission_level: str = "",
) -> dict[str, Any]:
    """Preview policy outcome for an MCP tool call without executing it."""
    payload = {
        "server_id": server_id,
        "tool_name": tool_name,
        "arguments": arguments or {},
    }
    if permission_level:
        payload["permission_level"] = permission_level
    decision = check_action(
        ActionKind.MCP_CALL,
        target=f"{server_id}/{tool_name}",
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        payload=payload,
    )
    return {
        "server_id": server_id,
        "tool": tool_name,
        "arguments": arguments or {},
        "allowed": decision.allowed,
        "requires_approval": decision.requires_approval,
        "approval_id": decision.approval_id,
        "permission_level": decision.permission_level,
        "risk": decision.risk,
        "reason": decision.reason,
    }
