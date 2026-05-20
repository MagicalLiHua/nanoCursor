"""Skill manifest parsing, validation, and version management."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _safe_skill_slug(skill_id: str) -> str:
    slug = skill_id.replace("skill.", "", 1) if skill_id.startswith("skill.") else skill_id
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"非法 Skill ID: {skill_id}")
    if not re.match(r"^[a-zA-Z0-9._-]+$", slug):
        raise ValueError(f"非法 Skill ID: {skill_id}")
    return slug


def _skill_dir(workspace: Path, skill_id: str) -> Path:
    slug = _safe_skill_slug(skill_id)
    return workspace / ".nanocursor" / "skills" / slug


def parse_skill_manifest(content: str) -> dict[str, Any]:
    """Extract YAML-like frontmatter from SKILL.md content."""
    manifest: dict[str, Any] = {"raw_content": content}
    if not content.startswith("---"):
        return manifest

    parts = content.split("---", 2)
    if len(parts) < 3:
        return manifest

    frontmatter = parts[1].strip()
    body = parts[2].strip()
    manifest["body"] = body

    current_list_key = ""
    for raw_line in frontmatter.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if current_list_key and stripped.startswith("- "):
            manifest.setdefault(current_list_key, []).append(stripped[2:].strip().strip('"').strip("'"))
            continue
        current_list_key = ""
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            # Parse lists: "[a, b]"
            if value.startswith("[") and value.endswith("]"):
                items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
                manifest[key] = items
            elif not value:
                manifest[key] = []
                current_list_key = key
            else:
                manifest[key] = value.strip('"').strip("'")
    return manifest


# Known capability ID prefixes
KNOWN_CAPABILITY_PREFIXES = frozenset({"tool.", "skill.", "mcp."})


def _is_valid_capability_id(cap_id: str) -> bool:
    """Check if a capability ID looks valid (has a known prefix)."""
    return any(str(cap_id).startswith(p) for p in KNOWN_CAPABILITY_PREFIXES)


def validate_skill_manifest(content: str) -> dict[str, Any]:
    """Formally validate a Skill frontmatter against the manifest schema.

    Required fields:
      - name (non-empty string)
    Recommended:
      - version (SemVer-like)
      - agents (non-empty list)
      - capabilities (list of known capability IDs)
      - risk_level (low / medium / high)
    """
    checks: list[dict[str, Any]] = []

    if not content or not content.strip():
        checks.append({"id": "not_empty", "status": "failed", "message": "Skill 内容不能为空。"})
        return {"ok": False, "checks": checks}

    checks.append({"id": "not_empty", "status": "passed", "message": "内容不为空。"})

    manifest = parse_skill_manifest(content)
    has_manifest = "body" in manifest
    checks.append({
        "id": "manifest",
        "status": "passed" if has_manifest else "info",
        "message": "已解析 frontmatter。" if has_manifest else "无 frontmatter（可选）。",
    })

    if not has_manifest:
        return {"ok": True, "checks": checks}

    # name (required)
    name = manifest.get("name", "")
    if name:
        checks.append({"id": "name", "status": "passed", "message": f"名称: {name}"})
    else:
        checks.append({"id": "name", "status": "failed", "message": "name 字段为必填，请在 frontmatter 中声明 name。"})

    # version (SemVer-recommended)
    version = str(manifest.get("version", ""))
    if version and re.match(r"\d+\.\d+\.\d+", version):
        checks.append({"id": "version", "status": "passed", "message": f"版本: {version}"})
    elif version:
        checks.append({"id": "version", "status": "warning", "message": f"版本格式建议使用 SemVer (x.y.z): {version}"})
    else:
        checks.append({"id": "version", "status": "info", "message": "未声明 version。"})

    # agents (must be non-empty list)
    agents = manifest.get("agents", [])
    if isinstance(agents, list) and len(agents) > 0:
        checks.append({"id": "agents", "status": "passed", "message": f"已声明 {len(agents)} 个 Agent。"})
    elif isinstance(agents, list):
        checks.append({"id": "agents", "status": "warning", "message": "agents 列表为空。"})
    else:
        # Parse string representation
        agents_str = str(manifest.get("agents", ""))
        if agents_str:
            checks.append({"id": "agents", "status": "warning", "message": "agents 应为 YAML 列表格式。"})

    # capabilities (must be known IDs)
    capabilities = manifest.get("capabilities", [])
    if isinstance(capabilities, list):
        known: list[str] = []
        unknown: list[str] = []
        for cap in capabilities:
            cap_str = str(cap)
            if _is_valid_capability_id(cap_str):
                known.append(cap_str)
            else:
                unknown.append(cap_str)
        if unknown:
            checks.append({
                "id": "capabilities",
                "status": "warning",
                "message": f"未知 capability ID: {', '.join(unknown)}。已知前缀: tool., skill., mcp.",
            })
        elif known:
            checks.append({
                "id": "capabilities",
                "status": "passed",
                "message": f"已声明 {len(known)} 个能力。",
            })
        else:
            checks.append({"id": "capabilities", "status": "info", "message": "未声明 capabilities。"})
    else:
        checks.append({"id": "capabilities", "status": "info", "message": "未声明 capabilities。"})

    # risk_level
    risk = str(manifest.get("risk_level", "")).lower()
    if risk in ("low", "medium", "high"):
        checks.append({"id": "risk_level", "status": "passed", "message": f"风险级别: {risk}"})
    elif risk:
        checks.append({
            "id": "risk_level",
            "status": "warning",
            "message": f"risk_level 应为 low/medium/high 之一，当前值: {risk}",
        })

    # Overall
    statuses = [c["status"] for c in checks]
    ok = "failed" not in statuses
    return {"ok": ok, "checks": checks, "manifest": manifest}


def validate_skill_content(content: str) -> dict[str, Any]:
    """Validate skill content format — delegates to validate_skill_manifest."""
    return validate_skill_manifest(content)


def save_skill_version(skill_id: str, content: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Save current skill content as a version before overwriting."""
    workspace = _workspace(workspace_dir)
    skill_dir_path = _skill_dir(workspace, skill_id)
    if not (skill_dir_path / "SKILL.md").exists():
        return {"ok": False, "message": "Skill 不存在。"}

    versions_dir = skill_dir_path / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    version_file = versions_dir / f"{ts}.md"
    shutil.copy(skill_dir_path / "SKILL.md", version_file)

    manifest_data = {"skill_id": skill_id, "version": ts, "saved_at": time.time()}
    (versions_dir / f"{ts}.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "version": ts, "skill_id": skill_id}


def list_skill_versions(skill_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """List all saved versions of a skill."""
    workspace = _workspace(workspace_dir)
    versions_dir = _skill_dir(workspace, skill_id) / "versions"
    if not versions_dir.exists():
        return {"skill_id": skill_id, "versions": [], "count": 0}

    versions: list[dict[str, Any]] = []
    for json_file in sorted(versions_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            versions.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "skill_id": skill_id,
        "versions": versions[:20],
        "count": len(versions),
    }


def restore_skill_version(skill_id: str, version_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Restore a skill to a previous version."""
    workspace = _workspace(workspace_dir)
    versions_dir = _skill_dir(workspace, skill_id) / "versions"
    version_file = versions_dir / f"{version_id}.md"
    if not version_file.exists():
        raise ValueError(f"版本不存在: {version_id}")

    current = _skill_dir(workspace, skill_id) / "SKILL.md"
    shutil.copy(version_file, current)
    return {"ok": True, "skill_id": skill_id, "restored_version": version_id}
