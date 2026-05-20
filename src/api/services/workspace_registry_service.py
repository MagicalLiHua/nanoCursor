"""Workspace registry: project identity, recent list, trusted state."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module


VERSION = "0.1.0"
SCHEMA_VERSION = 1


def _runtime_root() -> Path:
    root = Path(config_module.RUNTIME_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _recent_path() -> Path:
    return _runtime_root() / "recent.json"


def _workspace_json_path(workspace: Path) -> Path:
    nc = workspace / ".nanocursor"
    nc.mkdir(parents=True, exist_ok=True)
    return nc / "workspace.json"


def _workspace_id_from_path(abs_path: str) -> str:
    h = hashlib.md5(abs_path.encode()).hexdigest()[:12]
    return f"ws_{h}"


def get_workspace_identity(workspace_dir: str | None = None) -> dict[str, Any]:
    """Read workspace identity from workspace.json, or build a minimal one."""
    wdir = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    wp = _workspace_json_path(wdir)
    if wp.exists():
        try:
            identity = json.loads(wp.read_text(encoding="utf-8"))
            if isinstance(identity, dict):
                return _normalize_identity(identity, wdir)
        except (json.JSONDecodeError, OSError):
            pass
    return _normalize_identity({}, wdir)


def _normalize_identity(identity: dict[str, Any], wdir: Path, now: str | None = None) -> dict[str, Any]:
    current_path = str(wdir)
    previous_path = identity.get("path")
    try:
        schema_version = int(identity.get("schema_version") or SCHEMA_VERSION)
    except (TypeError, ValueError):
        schema_version = SCHEMA_VERSION
    normalized = {
        "workspace_id": identity.get("workspace_id") or _workspace_id_from_path(current_path),
        "name": identity.get("name") or wdir.name,
        "path": current_path,
        "trusted": bool(identity.get("trusted", False)),
        "created_at": identity.get("created_at") or (now or ""),
        "last_opened_at": identity.get("last_opened_at") or (now or ""),
        "nanocursor_version": identity.get("nanocursor_version") or VERSION,
        "schema_version": schema_version,
    }
    if previous_path and previous_path != current_path:
        normalized["previous_path"] = previous_path
    return normalized


def open_project(dir_path: str) -> dict[str, Any]:
    """Open a project directory: resolve path, write workspace.json, update recent list."""
    if not dir_path or not os.path.isabs(os.path.expanduser(dir_path)):
        raise ValueError("工作区路径必须是绝对路径。")

    abs_path = os.path.abspath(os.path.expanduser(dir_path))
    if not os.path.exists(abs_path):
        raise ValueError(f"路径不存在: {abs_path}")
    if not os.path.isdir(abs_path):
        raise ValueError(f"路径不是目录: {abs_path}")

    wdir = Path(abs_path)
    wp = _workspace_json_path(wdir)
    is_new = not wp.exists()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    identity = {}
    if wp.exists():
        try:
            identity = json.loads(wp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            identity = {}
    identity = _normalize_identity({**identity, "last_opened_at": now}, wdir, now=now)
    identity["nanocursor_version"] = VERSION
    identity["schema_version"] = SCHEMA_VERSION
    wp.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")

    # Update active workspace in config
    config_module.WORKSPACE_DIR = str(wdir)

    # Update recent list
    _add_to_recent(identity)

    return {**identity, "is_new": is_new}


def _add_to_recent(identity: dict[str, Any]) -> None:
    rp = _recent_path()
    recent: list[dict[str, Any]] = []
    if rp.exists():
        try:
            recent = json.loads(rp.read_text(encoding="utf-8"))
            if not isinstance(recent, list):
                recent = []
        except (json.JSONDecodeError, OSError):
            recent = []

    # Remove existing entry for this path, then prepend
    recent = [r for r in recent if r.get("path") != identity["path"]]
    recent.insert(0, {
        "workspace_id": identity["workspace_id"],
        "name": identity["name"],
        "path": identity["path"],
        "last_opened_at": identity["last_opened_at"],
    })
    # Keep at most 20 entries
    recent = recent[:20]
    rp.write_text(json.dumps(recent, ensure_ascii=False, indent=2), encoding="utf-8")


def list_recent_projects() -> list[dict[str, Any]]:
    """Return the list of recently opened projects."""
    rp = _recent_path()
    if not rp.exists():
        return []
    try:
        items = json.loads(rp.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except (json.JSONDecodeError, OSError):
        return []
