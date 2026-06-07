"""Skill registry routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.models import (
    GitHubSkillImportPreviewRequest,
    GitHubSkillImportRequest,
    GitHubSkillUpdateApplyRequest,
    GitHubSkillUpdateRequest,
    SkillEnabledRequest,
    SkillImportRequest,
    SkillPreviewRequest,
    SkillUpdateRequest,
)
from src.api.services.skill_github_import_service import (
    apply_github_skill_update_async,
    check_github_skill_update_async,
    import_github_skill_async,
    preview_github_skill_import_async,
    preview_github_skill_update_async,
)
from src.api.services.skill_manifest_service import (
    list_skill_versions,
    restore_skill_version,
    save_skill_version,
    validate_skill_content,
)
from src.api.services.skill_registry_service import (
    get_skill,
    import_skill,
    list_skills,
    preview_skill_selection,
    set_skill_enabled,
)
from src.api.services.skill_service import delete_workspace_skill, update_workspace_skill


router = APIRouter(tags=["skills"])


def _get_workspace() -> str:
    import src.infra.config as config_module

    return config_module.WORKSPACE_DIR


@router.get("/api/skills")
async def get_skills(include_disabled: bool = True):
    return list_skills(_get_workspace(), include_disabled=include_disabled)


@router.get("/api/skills/{skill_id}")
async def get_skill_route(skill_id: str):
    try:
        return get_skill(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/skills/import")
async def import_skill_route(request: SkillImportRequest):
    try:
        skill = import_skill(
            request.name,
            request.content,
            _get_workspace(),
            description=request.description,
            skill_json=request.skill_json,
            enabled=request.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": skill, "registry": list_skills(_get_workspace())}


@router.put("/api/skills/{skill_id}")
async def update_skill_route(skill_id: str, request: SkillUpdateRequest):
    try:
        save_skill_version(skill_id, request.content, _get_workspace())
        detail = update_workspace_skill(skill_id, request.content, _get_workspace())
        # Re-normalize skill.json after content edit so registry metadata stays coherent.
        current = get_skill(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**detail, "registry": current}


@router.delete("/api/skills/{skill_id}")
async def delete_skill_route(skill_id: str):
    try:
        result = delete_workspace_skill(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": bool(result.get("ok", True)), "skill_id": skill_id}


@router.post("/api/skills/{skill_id}/enable")
async def enable_skill_route(skill_id: str):
    try:
        return set_skill_enabled(skill_id, True, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/skills/{skill_id}/disable")
async def disable_skill_route(skill_id: str):
    try:
        return set_skill_enabled(skill_id, False, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/skills/{skill_id}")
async def set_skill_enabled_route(skill_id: str, request: SkillEnabledRequest):
    try:
        return set_skill_enabled(skill_id, request.enabled, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/skills/preview")
async def preview_skills_route(request: SkillPreviewRequest):
    return preview_skill_selection(request.prompt, _get_workspace(), team=request.team)


@router.post("/api/skills/import/github/preview")
async def preview_github_skill_route(request: GitHubSkillImportPreviewRequest):
    try:
        return await preview_github_skill_import_async(
            request.repo_url,
            ref=request.ref,
            path=request.path,
            token=request.token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub Skill 预览失败: {exc}") from exc


@router.post("/api/skills/import/github")
async def import_github_skill_route(request: GitHubSkillImportRequest):
    try:
        skill = await import_github_skill_async(
            request.repo_url,
            ref=request.ref,
            path=request.path,
            candidate_id=request.candidate_id,
            workspace_dir=_get_workspace(),
            token=request.token,
            enabled=request.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub Skill 导入失败: {exc}") from exc
    return {"ok": True, "skill": skill, "registry": list_skills(_get_workspace())}


@router.post("/api/skills/{skill_id}/updates/check")
async def check_github_skill_update_route(skill_id: str, request: GitHubSkillUpdateRequest | None = None):
    request = request or GitHubSkillUpdateRequest()
    try:
        return await check_github_skill_update_async(skill_id, workspace_dir=_get_workspace(), token=request.token, ref=request.ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub Skill 更新检查失败: {exc}") from exc


@router.post("/api/skills/{skill_id}/updates/preview")
async def preview_github_skill_update_route(skill_id: str, request: GitHubSkillUpdateRequest | None = None):
    request = request or GitHubSkillUpdateRequest()
    try:
        return await preview_github_skill_update_async(skill_id, workspace_dir=_get_workspace(), token=request.token, ref=request.ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub Skill 更新预览失败: {exc}") from exc


@router.post("/api/skills/{skill_id}/updates/apply")
async def apply_github_skill_update_route(skill_id: str, request: GitHubSkillUpdateApplyRequest):
    try:
        result = await apply_github_skill_update_async(
            skill_id,
            workspace_dir=_get_workspace(),
            token=request.token,
            ref=request.ref,
            confirmed=request.confirmed,
            enabled=request.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub Skill 更新应用失败: {exc}") from exc
    return {**result, "registry": list_skills(_get_workspace())}


@router.post("/api/skills/{skill_id}/validate")
async def validate_skill_route(skill_id: str):
    try:
        detail = get_skill(skill_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return validate_skill_content(detail.get("content", ""))


@router.get("/api/skills/{skill_id}/versions")
async def get_skill_versions_route(skill_id: str):
    return list_skill_versions(skill_id, _get_workspace())


@router.post("/api/skills/{skill_id}/versions/{version_id}/restore")
async def restore_skill_version_route(skill_id: str, version_id: str):
    try:
        return restore_skill_version(skill_id, version_id, _get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
