"""Status helpers for the optional Go MCP gateway."""

from __future__ import annotations

from typing import Any, TypedDict

from src.runtime.runtime_feature_flags import (
    go_mcp_gateway_addr,
    go_mcp_gateway_enabled,
    go_mcp_gateway_fallback_enabled,
)


class GoMcpGatewayStatus(TypedDict, total=False):
    enabled: bool
    fallback_enabled: bool
    address: str
    healthy: bool
    backend: str
    service: str
    version: str
    error: str


def get_go_mcp_gateway_status() -> GoMcpGatewayStatus:
    """Return a small health snapshot without making Go MCP mandatory."""
    enabled = go_mcp_gateway_enabled()
    status: GoMcpGatewayStatus = {
        "enabled": enabled,
        "fallback_enabled": go_mcp_gateway_fallback_enabled(),
        "address": go_mcp_gateway_addr(),
        "healthy": False,
        "backend": "go" if enabled else "python",
    }
    if not enabled:
        return status

    try:
        from src.runtime import mcp_client

        previous_addr = getattr(mcp_client, "MCP_ADDR", "")
        if previous_addr != status["address"]:
            mcp_client.close()
            mcp_client.MCP_ADDR = status["address"]
        try:
            health: dict[str, Any] = mcp_client.health()
        finally:
            if previous_addr and previous_addr != status["address"]:
                mcp_client.close()
                mcp_client.MCP_ADDR = previous_addr

        status.update({
            "healthy": bool(health.get("ok")),
            "service": str(health.get("service") or ""),
            "version": str(health.get("version") or ""),
        })
    except Exception as exc:
        status["error"] = str(exc)
        if status["fallback_enabled"]:
            status["backend"] = "python"
    return status
