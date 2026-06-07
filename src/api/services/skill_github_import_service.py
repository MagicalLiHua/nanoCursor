"""GitHub static Skill import support.

Only static files are fetched and imported. The service never executes remote
scripts, never installs dependencies, and locks the imported Skill to a commit
plus checksum.
"""

from __future__ import annotations

import base64
import asyncio
import difflib
import hashlib
import json
import re
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from src.api.services.skill_manifest_service import save_skill_version
from src.api.services.skill_registry_service import get_skill, import_skill, scan_skill_content
from src.infra import config as config_module


ALLOWED_SKILL_FILES = {"SKILL.md", "skill.json", "examples.md", "README.md"}
DISCOVERY_PATHS = ["", "skills", ".codex/skills", ".claude/skills"]
MAX_FILE_CHARS = 80_000


@dataclass(frozen=True)
class GitHubSkillSource:
    owner: str
    repo: str
    ref: str
    path: str = ""

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_github_skill_url(repo_url: str, ref: str = "", path: str = "") -> GitHubSkillSource:
    """Parse GitHub repo URLs including optional /tree/ref/path form."""
    parsed = urlparse(str(repo_url or "").strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("只支持 GitHub HTTPS URL。")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub URL 需要包含 owner/repo。")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    parsed_ref = ref.strip() or "main"
    parsed_path = path.strip().strip("/")
    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        parsed_ref = ref.strip() or parts[3]
        parsed_path = path.strip().strip("/") or "/".join(parts[4:]).strip("/")
    _validate_github_component(owner, "owner")
    _validate_github_component(repo, "repo")
    if not re.match(r"^[A-Za-z0-9._/@-]+$", parsed_ref):
        raise ValueError("GitHub ref 包含非法字符。")
    if ".." in parsed_path or parsed_path.startswith("/"):
        raise ValueError("GitHub path 不合法。")
    return GitHubSkillSource(owner=owner, repo=repo, ref=parsed_ref, path=parsed_path)


def _validate_github_component(value: str, label: str) -> None:
    if not re.match(r"^[A-Za-z0-9_.-]+$", value):
        raise ValueError(f"GitHub {label} 不合法。")


def _api_get_json(url: str, token: str = "") -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nanoCursor-skill-importer",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _github_api_url(source: GitHubSkillSource, path: str = "") -> str:
    encoded_path = "/".join(quote(part) for part in path.strip("/").split("/") if part)
    suffix = f"/{encoded_path}" if encoded_path else ""
    return (
        f"https://api.github.com/repos/{source.owner}/{source.repo}"
        f"/contents{suffix}?ref={quote(source.ref)}"
    )


def _commit_sha(source: GitHubSkillSource, token: str = "") -> str:
    data = _api_get_json(
        f"https://api.github.com/repos/{source.owner}/{source.repo}/commits/{quote(source.ref)}",
        token=token,
    )
    return str(data.get("sha") or source.ref)


def _content_text(item: dict[str, Any], token: str = "") -> str:
    if item.get("encoding") == "base64" and item.get("content"):
        raw = base64.b64decode(str(item["content"]).encode("ascii"))
        return raw.decode("utf-8", errors="replace")[:MAX_FILE_CHARS]
    download_url = item.get("download_url")
    if not download_url:
        return ""
    request = Request(str(download_url), headers={"User-Agent": "nanoCursor-skill-importer"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=15) as response:
        return response.read(MAX_FILE_CHARS + 1).decode("utf-8", errors="replace")[:MAX_FILE_CHARS]


def _list_contents(source: GitHubSkillSource, path: str, token: str = "") -> list[dict[str, Any]]:
    data = _api_get_json(_github_api_url(source, path), token=token)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _collect_skill_files(source: GitHubSkillSource, base_path: str, token: str = "") -> dict[str, str]:
    items = _list_contents(source, base_path, token=token)
    if len(items) == 1 and items[0].get("type") == "file":
        item = items[0]
        if item.get("name") != "SKILL.md":
            return {}
        return {"SKILL.md": _content_text(item, token=token)}

    files: dict[str, str] = {}
    for item in items:
        if item.get("type") != "file":
            continue
        name = str(item.get("name") or "")
        if name not in ALLOWED_SKILL_FILES:
            continue
        files[name] = _content_text(item, token=token)
    if "SKILL.md" not in files:
        return {}
    return files


def _discover_skill_paths(source: GitHubSkillSource, token: str = "") -> list[str]:
    if source.path:
        return [source.path]
    discovered: list[str] = []
    for root in DISCOVERY_PATHS:
        try:
            items = _list_contents(source, root, token=token)
        except Exception:
            continue
        for item in items:
            name = str(item.get("name") or "")
            item_path = str(item.get("path") or name)
            if item.get("type") == "file" and name == "SKILL.md":
                discovered.append(root)
            elif item.get("type") == "dir":
                try:
                    child_names = {str(child.get("name") or "") for child in _list_contents(source, item_path, token=token)}
                except Exception:
                    child_names = set()
                if "SKILL.md" in child_names:
                    discovered.append(item_path)
    return sorted({path.strip("/") for path in discovered})


def preview_github_skill_import(
    repo_url: str,
    *,
    ref: str = "",
    path: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Preview static Skill candidates from a GitHub repository."""
    source = parse_github_skill_url(repo_url, ref=ref, path=path)
    commit = _commit_sha(source, token=token)
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for skill_path in _discover_skill_paths(source, token=token):
        try:
            files = _collect_skill_files(source, skill_path, token=token)
        except Exception as exc:
            errors.append({"path": skill_path, "error": str(exc)})
            continue
        if not files:
            continue
        skill_json = {}
        if files.get("skill.json"):
            try:
                parsed = json.loads(files["skill.json"])
                skill_json = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError as exc:
                errors.append({"path": skill_path, "error": f"skill.json 解析失败: {exc}"})
        name = str(skill_json.get("name") or _name_from_skill_md(files["SKILL.md"]) or skill_path.split("/")[-1] or source.repo)
        skill_id = str(skill_json.get("id") or _slug(name))
        scan = scan_skill_content(files["SKILL.md"], skill_json)
        candidates.append({
            "id": skill_id,
            "name": name,
            "path": skill_path,
            "files": sorted(files),
            "risk": scan["risk"],
            "findings": scan["findings"],
            "default_enabled": scan["default_enabled"],
            "allowed_permissions": scan["allowed_permissions"],
            "blocked_permissions": scan["blocked_permissions"],
            "source": {
                "type": "github",
                "repo": source.repo_full_name,
                "repo_url": repo_url,
                "path": skill_path,
                "ref": source.ref,
                "commit": commit,
                "previewed_at": time.time(),
            },
            "skill_json": skill_json,
            "content_preview": files["SKILL.md"][:1200],
            "_files": files,
        })

    public_candidates = [{k: v for k, v in item.items() if k != "_files"} for item in candidates]
    return {
        "ok": bool(public_candidates),
        "source": {
            "type": "github",
            "repo": source.repo_full_name,
            "repo_url": repo_url,
            "ref": source.ref,
            "commit": commit,
            "path": source.path,
        },
        "candidates": public_candidates,
        "errors": errors,
    }


async def preview_github_skill_import_async(
    repo_url: str,
    *,
    ref: str = "",
    path: str = "",
    token: str = "",
) -> dict[str, Any]:
    """Async boundary for GitHub Skill preview network I/O."""
    return await asyncio.to_thread(
        preview_github_skill_import,
        repo_url,
        ref=ref,
        path=path,
        token=token,
    )


def import_github_skill(
    repo_url: str,
    *,
    ref: str = "",
    path: str = "",
    candidate_id: str = "",
    workspace_dir: str | None = None,
    token: str = "",
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Import one static Skill candidate from GitHub into the workspace."""
    preview = preview_github_skill_import(repo_url, ref=ref, path=path, token=token)
    candidates = preview.get("candidates", [])
    if not candidates:
        raise ValueError("未发现可导入的 GitHub Skill。")

    selected_public = None
    for item in candidates:
        if not candidate_id or item.get("id") == candidate_id or item.get("path") == candidate_id:
            selected_public = item
            break
    if selected_public is None:
        raise ValueError(f"未找到候选 Skill: {candidate_id}")

    source = parse_github_skill_url(repo_url, ref=ref, path=path)
    files = _collect_skill_files(source, selected_public["path"], token=token)
    commit = preview["source"]["commit"]
    source_payload = {
        **selected_public["source"],
        "commit": commit,
        "imported_at": time.time(),
        "original_files": sorted(files),
    }
    skill_json = dict(selected_public.get("skill_json") or {})
    skill_json.setdefault("id", selected_public["id"])
    skill_json.setdefault("name", selected_public["name"])
    return import_skill(
        selected_public["name"],
        files["SKILL.md"],
        workspace_dir,
        skill_json=skill_json,
        source=source_payload,
        enabled=enabled,
        extra_files={key: value for key, value in files.items() if key != "SKILL.md"},
    )


async def import_github_skill_async(
    repo_url: str,
    *,
    ref: str = "",
    path: str = "",
    candidate_id: str = "",
    workspace_dir: str | None = None,
    token: str = "",
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Async boundary for GitHub Skill import network and filesystem I/O."""
    return await asyncio.to_thread(
        import_github_skill,
        repo_url,
        ref=ref,
        path=path,
        candidate_id=candidate_id,
        workspace_dir=workspace_dir,
        token=token,
        enabled=enabled,
    )


def check_github_skill_update(
    skill_id: str,
    *,
    workspace_dir: str | None = None,
    token: str = "",
    ref: str = "",
) -> dict[str, Any]:
    """Check whether a GitHub-imported Skill has a newer source commit."""
    current = _github_skill_source(skill_id, workspace_dir)
    source = _source_from_payload(current["source"], ref=ref)
    latest_commit = _commit_sha(source, token=token)
    current_commit = str(current["source"].get("commit") or "")
    changed = bool(latest_commit and latest_commit != current_commit)
    return {
        "ok": True,
        "skill_id": skill_id,
        "source": _public_source(current["source"]),
        "current_commit": current_commit,
        "latest_commit": latest_commit,
        "changed": changed,
        "status": "update_available" if changed else "up_to_date",
    }


async def check_github_skill_update_async(
    skill_id: str,
    *,
    workspace_dir: str | None = None,
    token: str = "",
    ref: str = "",
) -> dict[str, Any]:
    """Async boundary for GitHub Skill update checks."""
    return await asyncio.to_thread(
        check_github_skill_update,
        skill_id,
        workspace_dir=workspace_dir,
        token=token,
        ref=ref,
    )


def preview_github_skill_update(
    skill_id: str,
    *,
    workspace_dir: str | None = None,
    token: str = "",
    ref: str = "",
) -> dict[str, Any]:
    """Preview a GitHub Skill update with static diff and safety scan."""
    current = _github_skill_source(skill_id, workspace_dir)
    source = _source_from_payload(current["source"], ref=ref)
    latest_commit = _commit_sha(source, token=token)
    files = _collect_skill_files(source, source.path, token=token)
    if not files:
        raise ValueError("GitHub 来源未发现可更新的静态 Skill 文件。")

    skill_json = _parse_skill_json(files.get("skill.json", ""))
    scan = scan_skill_content(files["SKILL.md"], skill_json)
    current_files = _local_skill_files(current["skill_dir"])
    diff = _files_diff(current_files, files)
    checksum = _checksum(files)
    current_checksum = str(current["source"].get("checksum") or "")
    changed = latest_commit != str(current["source"].get("commit") or "") or checksum != current_checksum
    return {
        "ok": True,
        "skill_id": skill_id,
        "changed": changed,
        "status": "update_available" if changed else "up_to_date",
        "source": _public_source(current["source"]),
        "current_commit": str(current["source"].get("commit") or ""),
        "latest_commit": latest_commit,
        "current_checksum": current_checksum,
        "latest_checksum": checksum,
        "files": sorted(files),
        "diff": diff,
        "risk": scan["risk"],
        "findings": scan["findings"],
        "allowed_permissions": scan["allowed_permissions"],
        "blocked_permissions": scan["blocked_permissions"],
        "default_enabled": scan["default_enabled"],
        "skill_json": skill_json,
        "content_preview": files["SKILL.md"][:1200],
    }


async def preview_github_skill_update_async(
    skill_id: str,
    *,
    workspace_dir: str | None = None,
    token: str = "",
    ref: str = "",
) -> dict[str, Any]:
    """Async boundary for GitHub Skill update previews."""
    return await asyncio.to_thread(
        preview_github_skill_update,
        skill_id,
        workspace_dir=workspace_dir,
        token=token,
        ref=ref,
    )


def apply_github_skill_update(
    skill_id: str,
    *,
    workspace_dir: str | None = None,
    token: str = "",
    ref: str = "",
    confirmed: bool = False,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Apply a previewed GitHub Skill update after explicit confirmation."""
    if not confirmed:
        raise ValueError("GitHub Skill 更新需要 confirmed=true 确认。")
    current = _github_skill_source(skill_id, workspace_dir)
    preview = preview_github_skill_update(skill_id, workspace_dir=workspace_dir, token=token, ref=ref)
    source = _source_from_payload(current["source"], ref=ref)
    files = _collect_skill_files(source, source.path, token=token)
    if not files:
        raise ValueError("GitHub 来源未发现可更新的静态 Skill 文件。")
    if current.get("content"):
        save_skill_version(skill_id, current["content"], str(current["workspace"]))
    skill_json = _parse_skill_json(files.get("skill.json", ""))
    skill_json.setdefault("id", skill_id.replace("skill.", "", 1))
    skill_json.setdefault("name", _name_from_skill_md(files["SKILL.md"]) or current["detail"].get("name") or skill_id)
    if enabled is None:
        enabled = False if preview.get("risk") in {"high", "critical"} else bool(current["detail"].get("enabled", True))

    source_payload = {
        **current["source"],
        "ref": source.ref,
        "path": source.path,
        "commit": preview["latest_commit"],
        "checksum": preview["latest_checksum"],
        "updated_at": time.time(),
        "original_files": sorted(files),
    }
    skill = import_skill(
        str(skill_json.get("name") or current["detail"].get("name") or skill_id),
        files["SKILL.md"],
        str(current["workspace"]),
        skill_json=skill_json,
        source=source_payload,
        enabled=enabled,
        extra_files={key: value for key, value in files.items() if key != "SKILL.md"},
    )
    return {
        "ok": True,
        "updated": True,
        "skill": skill,
        "preview": {key: value for key, value in preview.items() if key != "content_preview"},
    }


async def apply_github_skill_update_async(
    skill_id: str,
    *,
    workspace_dir: str | None = None,
    token: str = "",
    ref: str = "",
    confirmed: bool = False,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Async boundary for GitHub Skill update application."""
    return await asyncio.to_thread(
        apply_github_skill_update,
        skill_id,
        workspace_dir=workspace_dir,
        token=token,
        ref=ref,
        confirmed=confirmed,
        enabled=enabled,
    )


def _workspace(workspace_dir: str | None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skill_slug(skill_id: str) -> str:
    raw = skill_id.replace("skill.", "", 1) if skill_id.startswith("skill.") else skill_id
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError(f"Skill ID 不合法: {skill_id}")
    return raw


def _github_skill_source(skill_id: str, workspace_dir: str | None) -> dict[str, Any]:
    workspace = _workspace(workspace_dir)
    detail = get_skill(skill_id, str(workspace))
    slug = _skill_slug(skill_id)
    skill_dir = workspace / ".nanocursor" / "skills" / slug
    source_path = skill_dir / "source.json"
    if not source_path.exists():
        raise ValueError(f"Skill 不是 GitHub 导入来源: {skill_id}")
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Skill 来源记录不可读: {skill_id}") from exc
    if not isinstance(source, dict) or source.get("type") != "github":
        raise ValueError(f"Skill 不是 GitHub 导入来源: {skill_id}")
    return {
        "workspace": workspace,
        "skill_dir": skill_dir,
        "detail": detail,
        "content": detail.get("content", ""),
        "source": source,
    }


def _source_from_payload(payload: dict[str, Any], *, ref: str = "") -> GitHubSkillSource:
    repo = str(payload.get("repo") or "")
    if "/" not in repo:
        repo_url = str(payload.get("repo_url") or "")
        parsed = parse_github_skill_url(repo_url, ref=ref or str(payload.get("ref") or ""), path=str(payload.get("path") or ""))
        return parsed
    owner, repo_name = repo.split("/", 1)
    return GitHubSkillSource(
        owner=owner,
        repo=repo_name,
        ref=ref or str(payload.get("ref") or "main"),
        path=str(payload.get("path") or "").strip("/"),
    )


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    allowed = {"type", "repo", "repo_url", "path", "ref", "commit", "checksum", "imported_at", "updated_at", "original_files"}
    return {key: value for key, value in source.items() if key in allowed}


def _parse_skill_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _local_skill_files(skill_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for name in ALLOWED_SKILL_FILES:
        path = skill_dir / name
        if not path.exists() or not path.is_file():
            continue
        try:
            files[name] = path.read_text(encoding="utf-8")[:MAX_FILE_CHARS]
        except OSError:
            continue
    return files


def _checksum(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(files[name].encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _files_diff(old_files: dict[str, str], new_files: dict[str, str]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for name in sorted(set(old_files) | set(new_files)):
        old = old_files.get(name, "")
        new = new_files.get(name, "")
        if old == new:
            continue
        patch = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
                lineterm="",
            )
        )
        diffs.append({
            "file": name,
            "status": "added" if name not in old_files else "removed" if name not in new_files else "modified",
            "old_chars": len(old),
            "new_chars": len(new),
            "patch": patch[:12000],
        })
    return diffs


def _name_from_skill_md(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.strip("# ").strip()
    return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug or "github-skill"
