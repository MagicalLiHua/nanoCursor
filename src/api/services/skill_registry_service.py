"""Skill registry, selection preview, safety scanning, and imports."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from src.api.services.capability_service import SKILL_TEMPLATES
from src.api.services.skill_manifest_service import parse_skill_manifest, validate_skill_manifest
from src.infra import config as config_module
from src.infra.path_guard import safe_slug


DEFAULT_CONTEXT_BUDGET = 1200
DEFAULT_APPROVAL_LEVELS = ["risky_write", "shell_risky", "mcp_write", "git_risky"]
SAFE_PERMISSION_LEVELS = {"read_only", "safe_write", "shell_safe"}


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skills_root(workspace: Path) -> Path:
    root = workspace / ".nanocursor" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skill_slug(value: str) -> str:
    raw = value.replace("skill.", "", 1) if value.startswith("skill.") else value
    slug = safe_slug(raw.strip().lower() or "skill", max_length=80)
    if not slug:
        raise ValueError("Skill 名称不能为空。")
    return slug


def _skill_id(slug: str) -> str:
    return f"skill.{slug}"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _checksum(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(files[name].encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def scan_skill_content(content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Static safety scan for external or workspace Skill content."""
    metadata = metadata or {}
    text = "\n".join([
        content or "",
        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    ]).lower()

    rules = [
        ("secret_access", "high", ("token", "secret", "api_key", "private key", "environment variable", "环境变量", "密钥")),
        ("delete_files", "high", ("rm -rf", "delete file", "remove directory", "删除", "清空目录")),
        ("shell_risky", "medium", ("curl | sh", "wget | sh", "install dependency", "npm install", "pip install", "执行 shell", "安装依赖")),
        ("git_risky", "high", ("git push", "force push", "git reset --hard", "改写历史", "提交或推送")),
        ("approval_bypass", "critical", ("bypass approval", "ignore approval", "disable safety", "绕过审批", "忽略安全")),
        ("network_access", "medium", ("http://", "https://", "network request", "外部网络")),
    ]
    findings: list[dict[str, Any]] = []
    for rule_id, severity, patterns in rules:
        for pattern in patterns:
            if pattern in text:
                findings.append({
                    "rule_id": rule_id,
                    "severity": severity,
                    "message": f"Skill 内容命中风险规则: {rule_id}",
                    "evidence": pattern,
                })
                break

    if any(item["severity"] == "critical" for item in findings):
        risk = "critical"
    elif any(item["severity"] == "high" for item in findings):
        risk = "high"
    elif any(item["severity"] == "medium" for item in findings):
        risk = "medium"
    else:
        risk = "low"

    requested_permissions = set(_as_list(metadata.get("tool_permissions")))
    if not requested_permissions:
        requested_permissions = {"read_only"}
    if risk in {"high", "critical"}:
        allowed_permissions = ["read_only"]
    else:
        allowed_permissions = sorted(permission for permission in requested_permissions if permission in SAFE_PERMISSION_LEVELS)
        allowed_permissions = allowed_permissions or ["read_only"]

    blocked_permissions = sorted({
        "shell_risky",
        "mcp_write",
        "git_risky",
        "risky_write",
        *(permission for permission in requested_permissions if permission not in SAFE_PERMISSION_LEVELS),
    })

    return {
        "risk": risk,
        "findings": findings,
        "default_enabled": risk == "low",
        "allowed_permissions": allowed_permissions,
        "blocked_permissions": blocked_permissions,
        "requested_permissions": sorted(requested_permissions),
    }


def _normalize_skill_json(
    slug: str,
    content: str,
    raw: dict[str, Any] | None = None,
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(raw or {})
    manifest = parse_skill_manifest(content)
    name = str(raw.get("name") or manifest.get("name") or slug.replace("-", " ").title()).strip()
    description = str(raw.get("description") or manifest.get("description") or "").strip()
    triggers = _as_list(raw.get("triggers") or manifest.get("triggers") or manifest.get("keywords"))
    anti_triggers = _as_list(raw.get("anti_triggers") or manifest.get("anti_triggers"))
    agent_roles = _as_list(raw.get("agent_roles") or raw.get("agents") or manifest.get("agents"))
    permissions = _as_list(raw.get("tool_permissions") or manifest.get("tool_permissions") or manifest.get("permissions"))
    scan = scan_skill_content(content, {**raw, "tool_permissions": permissions})

    enabled = bool(raw.get("enabled", scan["default_enabled"]))
    if scan["risk"] in {"high", "critical"} and raw.get("enabled") is not True:
        enabled = False

    return {
        "id": _skill_id(slug),
        "name": name,
        "description": description,
        "version": str(raw.get("version") or manifest.get("version") or "0.1.0"),
        "enabled": enabled,
        "scope": str(raw.get("scope") or "workspace"),
        "triggers": triggers,
        "anti_triggers": anti_triggers,
        "agent_roles": agent_roles or ["lead"],
        "tool_permissions": scan["allowed_permissions"],
        "requested_tool_permissions": scan["requested_permissions"],
        "blocked_permissions": scan["blocked_permissions"],
        "approval_required_levels": _as_list(raw.get("approval_required_levels")) or DEFAULT_APPROVAL_LEVELS,
        "context_budget": int(raw.get("context_budget") or DEFAULT_CONTEXT_BUDGET),
        "quality_rules": _as_list(raw.get("quality_rules") or manifest.get("quality_rules")),
        "risk": scan["risk"],
        "safety_findings": scan["findings"],
        "source": source or raw.get("source") or {"type": "workspace"},
        "updated_at": time.time(),
    }


def list_skills(workspace_dir: str | None = None, *, include_disabled: bool = True) -> dict[str, Any]:
    """List built-in and workspace Skills using the normalized registry shape."""
    workspace = _workspace(workspace_dir)
    skills: list[dict[str, Any]] = []

    for template in SKILL_TEMPLATES:
        skills.append({
            "id": template["id"],
            "name": template["name"],
            "description": template.get("description", ""),
            "version": "builtin",
            "enabled": True,
            "status": "ready",
            "scope": "builtin",
            "source": {"type": "builtin"},
            "path": "",
            "triggers": list(template.get("tags", [])),
            "anti_triggers": [],
            "agent_roles": [str(agent).lower() for agent in template.get("agents", [])],
            "tool_permissions": ["read_only"],
            "approval_required_levels": DEFAULT_APPROVAL_LEVELS,
            "context_budget": DEFAULT_CONTEXT_BUDGET,
            "quality_rules": list(template.get("outputs", [])),
            "risk": "low",
            "safety_findings": [],
        })

    root = _skills_root(workspace)
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        slug = skill_dir.name
        content = _read_text(skill_md)
        raw = _read_json(skill_dir / "skill.json")
        source = _read_json(skill_dir / "source.json") or raw.get("source") or {"type": "workspace"}
        item = _normalize_skill_json(slug, content, raw, source=source)
        item["status"] = "configured" if item["enabled"] else "disabled"
        item["path"] = str(skill_md)
        item["source_path"] = str(skill_md.relative_to(workspace))
        item["validation"] = validate_skill_manifest(content)
        if include_disabled or item["enabled"]:
            skills.append(item)

    summary = {
        "total": len(skills),
        "enabled": sum(1 for item in skills if item.get("enabled")),
        "workspace": sum(1 for item in skills if item.get("scope") != "builtin"),
        "builtin": sum(1 for item in skills if item.get("scope") == "builtin"),
        "high_risk": sum(1 for item in skills if item.get("risk") in {"high", "critical"}),
    }
    return {
        "workspace_dir": str(workspace),
        "skills": skills,
        "summary": summary,
    }


def get_skill(skill_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    for skill in list_skills(workspace_dir, include_disabled=True)["skills"]:
        if skill["id"] == skill_id:
            if skill.get("path"):
                skill["content"] = _read_text(Path(skill["path"]))
            return skill
    raise ValueError(f"Skill 不存在: {skill_id}")


def import_skill(
    name: str,
    content: str,
    workspace_dir: str | None = None,
    *,
    description: str = "",
    skill_json: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    enabled: bool | None = None,
    extra_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Import a static Skill into the workspace registry."""
    workspace = _workspace(workspace_dir)
    slug = _skill_slug(str((skill_json or {}).get("id") or name))
    skill_dir = _skills_root(workspace) / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    body = (content or "").strip() or description.strip() or f"# {name}\n\n{name} Skill."
    if not body.startswith("#") and not body.startswith("---"):
        body = f"# {name.strip()}\n\n{body}"

    metadata = dict(skill_json or {})
    if description and not metadata.get("description"):
        metadata["description"] = description
    normalized = _normalize_skill_json(slug, body, metadata, source=source)
    if enabled is not None:
        normalized["enabled"] = bool(enabled)
    if normalized["risk"] in {"high", "critical"} and enabled is None:
        normalized["enabled"] = False
    normalized["status"] = "configured" if normalized["enabled"] else "disabled"

    files = {"SKILL.md": body, "skill.json": json.dumps(normalized, ensure_ascii=False, sort_keys=True)}
    for filename, text in (extra_files or {}).items():
        if filename in {"SKILL.md", "skill.json", "source.json"}:
            continue
        files[filename] = text
    normalized.setdefault("source", source or {"type": "workspace"})
    source_payload = dict(normalized["source"])
    source_payload.setdefault("checksum", _checksum(files))

    (skill_dir / "SKILL.md").write_text(body.rstrip() + "\n", encoding="utf-8")
    _write_json(skill_dir / "skill.json", normalized)
    _write_json(skill_dir / "source.json", source_payload)
    for filename, text in (extra_files or {}).items():
        if filename in {"SKILL.md", "skill.json", "source.json"}:
            continue
        safe_name = safe_slug(filename.replace("/", "-"), max_length=120)
        if safe_name:
            (skill_dir / safe_name).write_text(text, encoding="utf-8")

    return get_skill(_skill_id(slug), str(workspace))


def set_skill_enabled(skill_id: str, enabled: bool, workspace_dir: str | None = None) -> dict[str, Any]:
    workspace = _workspace(workspace_dir)
    slug = _skill_slug(skill_id)
    skill_dir = _skills_root(workspace) / slug
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValueError(f"Skill 不存在: {skill_id}")
    content = _read_text(skill_md)
    raw = _read_json(skill_dir / "skill.json")
    raw["enabled"] = bool(enabled)
    normalized = _normalize_skill_json(slug, content, raw, source=_read_json(skill_dir / "source.json") or raw.get("source"))
    normalized["enabled"] = bool(enabled)
    normalized["status"] = "configured" if enabled else "disabled"
    _write_json(skill_dir / "skill.json", normalized)
    return get_skill(_skill_id(slug), str(workspace))


def preview_skill_selection(
    prompt: str,
    workspace_dir: str | None = None,
    *,
    team: list[dict[str, Any]] | None = None,
    max_skills: int = 5,
) -> dict[str, Any]:
    """Preview which Skills would be injected for a prompt."""
    prompt_lower = (prompt or "").lower()
    prompt_words = re.findall(r"[\w\u4e00-\u9fff]{2,}", prompt_lower)
    team_roles = {
        str(member.get("role", "")).lower()
        for member in (team or [])
        if str(member.get("role", "")).strip()
    }
    candidates: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    for skill in list_skills(workspace_dir, include_disabled=True)["skills"]:
        score = 0
        reasons: list[str] = []
        omitted_reasons: list[str] = []
        haystack = " ".join([
            str(skill.get("name", "")),
            str(skill.get("description", "")),
            " ".join(skill.get("triggers", [])),
        ]).lower()
        for trigger in skill.get("triggers", []):
            t = str(trigger).lower()
            if t and t in prompt_lower:
                score += 3
                reasons.append(f"命中 trigger: {trigger}")
        for word in prompt_words:
            if word in haystack:
                score += 1
        prompt_score = score
        for anti in skill.get("anti_triggers", []):
            if str(anti).lower() in prompt_lower:
                score -= 5
                omitted_reasons.append(f"命中 anti-trigger: {anti}")
        role_matches = team_roles & {str(role).lower() for role in skill.get("agent_roles", [])}
        if role_matches and prompt_score > 0:
            score += 2 * len(role_matches)
            reasons.append("匹配 Agent 角色: " + ", ".join(sorted(role_matches)))
        if not skill.get("enabled", True):
            omitted_reasons.append("skill disabled")
        if score <= 0 and not omitted_reasons:
            if prompt_words or team_roles:
                omitted_reasons.append("no trigger or role match")
            else:
                omitted_reasons.append("empty prompt")

        audit_item = {
            "id": skill["id"],
            "name": skill["name"],
            "score": score,
            "selection_reasons": reasons,
            "context_budget": skill.get("context_budget", DEFAULT_CONTEXT_BUDGET),
            "risk": skill.get("risk", "low"),
            "tool_permissions": skill.get("tool_permissions", []),
            "enabled": bool(skill.get("enabled", True)),
            "scope": skill.get("scope", "workspace"),
        }
        if omitted_reasons:
            omitted.append({
                **audit_item,
                "reason": "; ".join(omitted_reasons),
            })
            continue
        if score > 0:
            candidates.append({**audit_item, "selection_reasons": reasons or ["关键词相似"]})

    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = candidates[:max_skills]
    for item in candidates[max_skills:]:
        omitted.append({**item, "reason": "budget exceeded"})
    return {
        "prompt": prompt,
        "selected": selected,
        "omitted": omitted,
        "summary": {
            "selected": len(selected),
            "candidates": len(candidates),
            "omitted": len(omitted),
            "context_budget": sum(int(item.get("context_budget") or 0) for item in selected),
        },
    }
