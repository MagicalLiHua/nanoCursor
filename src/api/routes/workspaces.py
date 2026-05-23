"""Workspace routes — list, open, settings, health, observability."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from src.api.dependencies import get_workspace, raise_404
from src.api.models import (
    OpenWorkspaceRequest,
    SetWorkspaceRequest,
    WorkspaceHealth,
    WorkspaceSettings,
    WorkspaceSettingsUpdateRequest,
    WorkspaceSettingsValidateRequest,
)
from src.api.services.observability_service import build_workspace_observability
from src.api.services.migration_service import (
    inspect_workspace_migrations,
    migrate_workspace,
)
from src.api.services.workspace_registry_service import (
    list_recent_projects,
    open_project,
)
from src.api.services.workspace_runtime_service import (
    get_active_workspace,
    set_active_workspace,
)
from src.api.services.workspace_service import (
    build_workspace_health,
    build_workspace_overview,
)
from src.api.services.workspace_settings_service import (
    get_effective_settings,
    get_workspace_settings,
    save_workspace_settings,
    validate_settings,
)

router = APIRouter(prefix="/api", tags=["workspace"])


@router.get("/workspaces")
async def list_workspaces():
    import src.infra.config as config_module

    root = getattr(config_module, "WORKSPACE_ROOT",
                   os.path.join(config_module.PROJECT_ROOT, ".nanocursor", "workspaces"))
    workspaces: list[str] = []
    workspace_entries: list[dict] = []
    try:
        os.makedirs(root, exist_ok=True)
        for entry in os.listdir(root):
            path = os.path.join(root, entry)
            if os.path.isdir(path):
                workspaces.append(entry)
                workspace_entries.append({
                    "name": entry,
                    "path": os.path.abspath(path),
                    "is_current": os.path.abspath(path) == os.path.abspath(get_active_workspace()),
                })
        workspaces.sort()
        workspace_entries.sort(key=lambda item: item["name"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取工作区失败: {exc!s}")

    current = get_active_workspace()
    default_workspace = getattr(config_module, "DEFAULT_WORKSPACE_DIR", "")
    return {
        "workspaces": workspaces,
        "workspace_entries": workspace_entries,
        "current_workspace": current,
        "default_workspace": default_workspace,
        "workspace_root": root,
        "project_root": config_module.PROJECT_ROOT,
        "is_default_workspace": os.path.abspath(current) == os.path.abspath(default_workspace),
    }


@router.get("/workspace/overview")
async def get_workspace_overview(workspace_dir: str | None = None):
    return build_workspace_overview(workspace_dir or get_active_workspace())


@router.get("/workspace/recent")
async def get_recent_projects():
    return {"recent": list_recent_projects()}


@router.get("/workspace/settings")
async def get_settings():
    return WorkspaceSettings(**get_workspace_settings(get_active_workspace()))


@router.put("/workspace/settings")
async def update_settings(request: WorkspaceSettingsUpdateRequest):
    return save_workspace_settings(request.settings, get_active_workspace())


@router.get("/workspace/settings/effective")
async def get_effective():
    return get_effective_settings(get_active_workspace())


@router.post("/workspace/settings/validate")
async def validate_workspace_settings(request: WorkspaceSettingsValidateRequest | None = None):
    return validate_settings(request.settings if request else None, get_active_workspace())


@router.get("/workspace/health")
async def get_workspace_health(workspace_dir: str | None = None):
    return WorkspaceHealth(**build_workspace_health(workspace_dir or get_active_workspace()))


@router.get("/workspace/migration")
async def inspect_workspace_migration(workspace_dir: str | None = None):
    return inspect_workspace_migrations(workspace_dir or get_active_workspace())


@router.post("/workspace/migrate")
async def migrate_active_workspace(workspace_dir: str | None = None, dry_run: bool = False):
    try:
        return migrate_workspace(workspace_dir or get_active_workspace(), dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workspaces")
async def set_workspace(request: SetWorkspaceRequest):
    dir_path = request.dir
    if not dir_path:
        raise HTTPException(status_code=400, detail="工作目录路径不能为空")
    if not os.path.isabs(dir_path):
        raise HTTPException(status_code=400, detail="请输入绝对路径")
    try:
        identity = open_project(dir_path)
        set_active_workspace(dir_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "workspace_dir": identity["path"]}


@router.post("/workspaces/open")
async def open_workspace(request: OpenWorkspaceRequest):
    try:
        identity = open_project(request.path)
        set_active_workspace(request.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return identity


@router.get("/workspace/observability")
async def get_workspace_observability_route():
    return build_workspace_observability(get_active_workspace())
