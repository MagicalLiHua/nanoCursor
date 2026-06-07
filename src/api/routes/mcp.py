"""MCP routes: server registry, probes, tool catalog, previews, calls."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.models import (
    McpEnabledRequest,
    McpPresetInstallRequest,
    McpServerUpsertRequest,
    McpToolCallRequest,
)
from src.api.services.action_execution_service import execute_action_async
from src.api.services.mcp_runtime_service import list_all_mcp_tools, list_mcp_tools, probe_mcp_server
from src.api.services.mcp_service import (
    delete_mcp_server_config,
    install_mcp_server_preset,
    list_mcp_server_presets,
    list_mcp_servers,
    set_mcp_server_enabled,
    upsert_mcp_server_config,
)
from src.api.services.mcp_tool_catalog_service import preview_mcp_tool_call


router = APIRouter(tags=["mcp"])


def _get_workspace() -> str:
    import src.infra.config as config_module

    return config_module.WORKSPACE_DIR


@router.get("/api/mcp/servers")
async def get_mcp_servers():
    return list_mcp_servers(_get_workspace())


@router.post("/api/mcp/servers")
async def create_or_update_mcp_server(request: McpServerUpsertRequest):
    try:
        server = upsert_mcp_server_config(
            request.server_id,
            request.command,
            request.args,
            request.env_keys,
            workspace_dir=_get_workspace(),
            enabled=request.enabled,
            ignored_env_keys=request.ignored_env_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "server": server,
        "mcp": list_mcp_servers(_get_workspace()),
    }


@router.patch("/api/mcp/servers/{server_id}")
async def update_mcp_server_enabled(server_id: str, request: McpEnabledRequest):
    try:
        return set_mcp_server_enabled(server_id, request.enabled, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str):
    try:
        return delete_mcp_server_config(server_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/mcp/presets")
async def get_mcp_presets():
    return list_mcp_server_presets(_get_workspace())


@router.post("/api/mcp/presets/{preset_id}/install")
async def install_mcp_preset(
    preset_id: str,
    request: McpPresetInstallRequest | None = None,
):
    try:
        return install_mcp_server_preset(
            preset_id,
            _get_workspace(),
            enabled=request.enabled if request else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/mcp/servers/{server_id}/probe")
async def probe_mcp(server_id: str):
    return probe_mcp_server(server_id, _get_workspace())


@router.get("/api/mcp/tools")
async def get_mcp_tools(refresh: bool = False, include_disabled: bool = False):
    return list_all_mcp_tools(
        _get_workspace(),
        force_refresh=refresh,
        include_disabled=include_disabled,
    )


@router.get("/api/mcp/servers/{server_id}/tools")
async def get_mcp_server_tools(server_id: str, refresh: bool = False):
    return list_mcp_tools(server_id, _get_workspace(), force_refresh=refresh)


@router.post("/api/mcp/tools/preview")
async def preview_mcp_tool(request: McpToolCallRequest):
    server_id = request.server_id
    tool_name = request.tool_name
    arguments = request.arguments
    if not server_id or not tool_name:
        raise HTTPException(status_code=400, detail="preview 需要 server_id 和 tool_name")
    return preview_mcp_tool_call(
        server_id=server_id,
        tool_name=tool_name,
        arguments=arguments,
        workspace_dir=_get_workspace(),
        thread_id=request.thread_id,
        permission_level=request.permission_level,
    )


@router.post("/api/mcp/servers/{server_id}/tools/{tool_name}/call")
async def call_mcp(server_id: str, tool_name: str, request: McpToolCallRequest | None = None):
    data = request or McpToolCallRequest()
    payload = {
        "server_id": server_id,
        "tool_name": tool_name,
        "arguments": data.arguments,
        "timeout_seconds": data.timeout_seconds,
    }
    if data.approval_id:
        payload["approval_id"] = data.approval_id
    if data.permission_level:
        payload["permission_level"] = data.permission_level
    return await execute_action_async(
        kind="mcp_call",
        target=f"{server_id}/{tool_name}",
        payload=payload,
        thread_id=data.thread_id,
        workspace_dir=_get_workspace(),
    )
