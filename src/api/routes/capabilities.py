"""Capabilities routes: skills, MCP servers, team agents, preferences."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.models import (
    CapabilityRecommendRequest,
    McpEnabledRequest,
    McpPresetInstallRequest,
    McpServerUpsertRequest,
    McpToolCallRequest,
    McpValidateRequest,
    SkillImportRequest,
    SkillUpdateRequest,
    TeamAgentCreateRequest,
)
from src.api.services.agent_state import add_team_member, list_task_items, list_team_members
from src.api.services.capability_service import build_capability_hub, import_workspace_skill, recommend_capabilities
from src.api.services.ephemeral_agent_service import spawn_ephemeral_agent
from src.api.services.mcp_service import (
    install_mcp_server_preset,
    list_mcp_server_presets,
    list_mcp_servers,
    upsert_mcp_server_config,
    validate_mcp_config,
)
from src.api.services.mcp_status_service import get_mcp_server_status, get_mcp_status, set_mcp_enabled
from src.api.services.mcp_runtime_service import probe_mcp_server, list_mcp_tools, call_mcp_tool
from src.api.services.skill_manifest_service import (
    list_skill_versions, restore_skill_version, save_skill_version, validate_skill_content,
)
from src.api.services.skill_service import delete_workspace_skill, get_skill_detail, update_workspace_skill
from src.api.services.preference_service import add_preference_memory, build_memory_profile
from src.api.run_state import workspace_for_thread


router = APIRouter(tags=["capabilities"])


def _get_workspace() -> str:
    import src.infra.config as config_module
    return config_module.WORKSPACE_DIR


# --- Tasks & Team ---

@router.get("/api/tasks")
async def list_team_tasks():
    return {"tasks": list_task_items()}


@router.get("/api/team")
async def list_team_agents():
    return {"team": list_team_members()}


# --- Capabilities Hub ---

@router.get("/api/capabilities")
async def get_agenthub_capabilities():
    return build_capability_hub(_get_workspace())


@router.post("/api/capabilities/recommend")
async def recommend_caps(request: CapabilityRecommendRequest):
    return recommend_capabilities(request.prompt, _get_workspace())


# --- Skills ---

@router.post("/api/capabilities/skills")
async def create_skill(request: SkillImportRequest):
    skill = import_workspace_skill(request.name, request.description, request.content, _get_workspace())
    return {"skill": skill, "hub": build_capability_hub(_get_workspace()), "ok": True}


@router.get("/api/capabilities/skills/{skill_id}")
async def get_skill(skill_id: str):
    try:
        detail = get_skill_detail(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail


@router.put("/api/capabilities/skills/{skill_id}")
async def edit_skill(skill_id: str, request: SkillUpdateRequest):
    try:
        save_skill_version(skill_id, request.content, _get_workspace())
        detail = update_workspace_skill(skill_id, request.content, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail


@router.delete("/api/capabilities/skills/{skill_id}")
async def remove_skill(skill_id: str):
    try:
        result = delete_workspace_skill(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": bool(result.get("ok", True)), "skill_id": skill_id}


@router.post("/api/capabilities/skills/{skill_id}/validate")
async def validate_skill(skill_id: str):
    try:
        detail = get_skill_detail(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return validate_skill_content(detail.get("content", ""))


@router.get("/api/capabilities/skills/{skill_id}/versions")
async def get_skill_versions(skill_id: str):
    return list_skill_versions(skill_id, _get_workspace())


@router.post("/api/capabilities/skills/{skill_id}/versions/{version_id}/restore")
async def restore_skill_version_route(skill_id: str, version_id: str):
    try:
        return restore_skill_version(skill_id, version_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- MCP ---

@router.get("/api/capabilities/mcp")
async def get_agenthub_mcp_servers():
    return {"mcp": list_mcp_servers(_get_workspace())}


@router.post("/api/capabilities/mcp/validate")
async def validate_mcp_config_route(request: McpValidateRequest):
    return validate_mcp_config(request.server_id, _get_workspace())


@router.post("/api/capabilities/mcp/servers")
async def upsert_mcp_server(request: McpServerUpsertRequest):
    if not request.command.strip():
        raise HTTPException(status_code=400, detail="MCP command 不能为空")
    return upsert_mcp_server_config(
        request.server_id,
        request.command,
        request.args,
        request.env_keys,
        workspace_dir=_get_workspace(),
        enabled=request.enabled,
        ignored_env_keys=request.ignored_env_keys,
    )


@router.get("/api/capabilities/mcp/presets")
async def get_mcp_server_presets():
    return list_mcp_server_presets(_get_workspace())


@router.post("/api/capabilities/mcp/presets/{preset_id}/install")
async def install_mcp_server_preset_route(
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


@router.get("/api/capabilities/mcp/status")
async def get_mcp_route_status():
    return get_mcp_status(_get_workspace())


@router.get("/api/capabilities/mcp/{server_id}/status")
async def get_mcp_server_route_status(server_id: str):
    return get_mcp_server_status(server_id, _get_workspace())


@router.put("/api/capabilities/mcp/{server_id}/enabled")
async def set_mcp_enabled_route(server_id: str, data: McpEnabledRequest):
    return set_mcp_enabled(server_id, data.enabled, _get_workspace())


@router.post("/api/capabilities/mcp/{server_id}/probe")
async def probe_mcp_server_route(server_id: str):
    return probe_mcp_server(server_id, _get_workspace())


@router.get("/api/capabilities/mcp/{server_id}/tools")
async def list_mcp_tools_route(server_id: str, refresh: bool = False):
    return list_mcp_tools(server_id, _get_workspace(), force_refresh=refresh)


@router.post("/api/capabilities/mcp/{server_id}/tools/{tool_name}/call")
async def call_mcp_tool_route(server_id: str, tool_name: str, request: McpToolCallRequest | None = None):
    return call_mcp_tool(server_id, tool_name, request.arguments if request else {}, _get_workspace())


# --- Team Agents ---

@router.post("/api/team/agents")
async def create_team_agent(request: TeamAgentCreateRequest):
    try:
        agent = add_team_member(
            request.name,
            request.role,
            request.goal,
            request.tools,
            request.capabilities,
            _get_workspace(),
            source="user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent": agent}


@router.post("/api/lead/agents")
async def lead_create_agent(request: TeamAgentCreateRequest):
    """Create an Agent from Lead's delegation decision."""
    lifetime = str(request.lifetime or "permanent").lower()
    if lifetime not in {"permanent", "temporary"}:
        raise HTTPException(status_code=400, detail="lifetime must be permanent or temporary")

    if lifetime == "temporary":
        if not request.thread_id:
            raise HTTPException(status_code=400, detail="temporary Agent requires thread_id")
        workspace = workspace_for_thread(request.thread_id)
        spec = {
            "name": request.name,
            "role": request.role,
            "goal": request.goal,
            "reason": "Lead 按本轮任务需要创建临时 Agent。",
            "tools": request.tools,
            "capabilities": request.capabilities,
            "mcp_servers": request.mcp_servers,
            "blocked_capabilities": request.blocked_capabilities,
            "risk_level": request.risk_level,
            "task_scope": request.task_scope,
            "expected_output": request.expected_output,
            "ttl_seconds": request.ttl_seconds,
            "parent_agent": "Lead",
        }
        try:
            agent = spawn_ephemeral_agent(request.thread_id, spec, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"agent": agent, "lifetime": "temporary", "thread_id": request.thread_id}

    try:
        agent = add_team_member(
            request.name,
            request.role,
            request.goal,
            request.tools,
            request.capabilities,
            _get_workspace(),
            source="lead",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent": agent, "lifetime": "permanent"}


# --- Preferences ---

@router.get("/api/preferences/profile")
async def get_preference_profile():
    return build_memory_profile(_get_workspace())


@router.post("/api/preferences")
async def add_preference(request: PreferenceCreateRequest):
    try:
        mem = add_preference_memory(
            request.preference_type,
            request.content,
            request.importance,
            _get_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"memory": mem}
