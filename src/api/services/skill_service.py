"""Skill detail, preview, edit, and delete operations."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.capability_service import SKILL_TEMPLATES


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug_from_skill_id(skill_id: str) -> str:
    """Extract safe slug from skill_id like 'skill.my-skill-name'."""
    if not skill_id.startswith("skill."):
        raise ValueError(f"无效的 skill_id: {skill_id}")
    slug = skill_id[len("skill."):]
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"无效的 skill_id: {skill_id}")
    return slug


def _is_builtin(skill_id: str) -> bool:
    return any(t["id"] == skill_id for t in SKILL_TEMPLATES)


def _skill_file(workspace: Path, slug: str) -> Path:
    return workspace / ".nanocursor" / "skills" / slug / "SKILL.md"


def get_skill_detail(skill_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Get full detail for a skill, whether built-in or workspace."""
    workspace = _workspace(workspace_dir)
    slug = _slug_from_skill_id(skill_id)

    # Check built-in templates first
    for template in SKILL_TEMPLATES:
        if template["id"] == skill_id:
            return {
                "id": template["id"],
                "name": template["name"],
                "status": "ready",
                "source": "built-in",
                "path": "",
                "description": template.get("description", ""),
                "content": template.get("description", ""),
                "agents": template.get("agents", []),
                "use_cases": template.get("use_cases", []),
                "last_used_run_id": None,
            }

    # Check workspace skill
    skill_path = _skill_file(workspace, slug)
    if not skill_path.exists():
        raise ValueError(f"Skill 不存在: {skill_id}")

    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取 Skill 文件: {exc}") from exc

    first_line = content.splitlines()[0].strip("# ").strip() if content else slug

    return {
        "id": skill_id,
        "name": first_line or slug,
        "status": "configured",
        "source": str(skill_path.relative_to(workspace)),
        "path": str(skill_path),
        "description": content.strip()[:200],
        "content": content,
        "agents": ["Lead", "Coder"],
        "use_cases": ["项目专属工作流", "重复任务标准化", "团队经验沉淀"],
        "last_used_run_id": None,
    }


def update_workspace_skill(
    skill_id: str,
    content: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Update a workspace skill's SKILL.md content. Built-in skills are read-only."""
    if _is_builtin(skill_id):
        raise ValueError("内置 Skill 只能查看，不能编辑。")

    workspace = _workspace(workspace_dir)
    slug = _slug_from_skill_id(skill_id)
    skill_path = _skill_file(workspace, slug)

    if not skill_path.exists():
        raise ValueError(f"Skill 不存在: {skill_id}")

    try:
        skill_path.write_text(content + "\n", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法写入 Skill 文件: {exc}") from exc

    return get_skill_detail(skill_id, str(workspace))


def delete_workspace_skill(
    skill_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Delete a workspace skill directory. Built-in skills cannot be deleted."""
    if _is_builtin(skill_id):
        raise ValueError("内置 Skill 不能删除。")

    workspace = _workspace(workspace_dir)
    slug = _slug_from_skill_id(skill_id)
    skill_dir = workspace / ".nanocursor" / "skills" / slug

    if not skill_dir.exists():
        raise ValueError(f"Skill 不存在: {skill_id}")

    try:
        shutil.rmtree(skill_dir)
    except OSError as exc:
        raise ValueError(f"无法删除 Skill 目录: {exc}") from exc

    return {"ok": True, "skill_id": skill_id}
