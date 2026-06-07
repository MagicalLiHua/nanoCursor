"""Compatibility re-export for the Go MCP Gateway client."""

from src.runtime.go_mcp_gateway_client import (  # noqa: F401
    call_mcp_tool,
    list_mcp_presets,
    list_mcp_servers,
    list_mcp_tools,
    probe_mcp_server,
)
