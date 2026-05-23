"""Workspace metadata migration helpers.

The migration layer keeps old workspaces usable as nanoCursor's local data
model evolves.  It only touches files under ``<workspace>/.nanocursor`` and
backs up existing metadata before overwriting it.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from src.api.services.run_history import rebuild_run_index
from src.api.services.workspace_registry_service import (
    SCHEMA_VERSION,
    ensure_workspace_manifest,
)
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _workspace_manifest_path(workspace: Path) -> Path:
    return workspace / ".nanocursor" / "workspace.json"


def _run_index_path(workspace: Path) -> Path:
    return workspace / ".nanocursor" / "runs" / "index.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _count_legacy_runs(workspace: Path) -> int:
    runs_root = workspace / ".nanocursor" / "runs"
    if not runs_root.exists():
        return 0
    count = 0
    for run_dir in runs_root.iterdir():
        if run_dir.is_dir() and (run_dir / "session.json").exists():
            count += 1
    return count


def _manifest_status(workspace: Path) -> dict[str, Any]:
    path = _workspace_manifest_path(workspace)
    data = _read_json(path)
    schema_version = data.get("schema_version") if isinstance(data, dict) else None
    valid = isinstance(data, dict) and schema_version == SCHEMA_VERSION
    return {
        "path": str(path),
        "exists": path.exists(),
        "valid": valid,
        "schema_version": schema_version,
        "target_schema_version": SCHEMA_VERSION,
        "needs_migration": not valid,
    }


def _run_index_status(workspace: Path) -> dict[str, Any]:
    path = _run_index_path(workspace)
    data = _read_json(path)
    runs = data.get("runs") if isinstance(data, dict) else None
    valid = isinstance(data, dict) and data.get("schema_version") == 1 and isinstance(runs, list)
    return {
        "path": str(path),
        "exists": path.exists(),
        "valid": valid,
        "schema_version": data.get("schema_version") if isinstance(data, dict) else None,
        "run_count": len(runs) if isinstance(runs, list) else 0,
        "legacy_run_count": _count_legacy_runs(workspace),
        "needs_migration": not valid,
    }


def inspect_workspace_migrations(workspace_dir: str | None = None) -> dict[str, Any]:
    """Return migration status for one workspace without modifying files."""
    workspace = _workspace(workspace_dir)
    actions: list[str] = []
    manifest = _manifest_status(workspace)
    run_index = _run_index_status(workspace)

    if not workspace.exists():
        actions.append("workspace_missing")
    elif not workspace.is_dir():
        actions.append("workspace_not_directory")
    else:
        if manifest["needs_migration"]:
            actions.append("ensure_workspace_manifest")
        if run_index["needs_migration"]:
            actions.append("rebuild_run_index")

    return {
        "workspace_dir": str(workspace),
        "ok": workspace.exists() and workspace.is_dir() and not actions,
        "actions": actions,
        "manifest": manifest,
        "run_index": run_index,
    }


def _backup_existing_metadata(workspace: Path) -> dict[str, Any]:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_root = workspace / ".nanocursor" / "migrations" / timestamp
    copied: list[dict[str, str]] = []

    for source in (_workspace_manifest_path(workspace), _run_index_path(workspace)):
        if not source.exists():
            continue
        target = backup_root / source.relative_to(workspace / ".nanocursor")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"source": str(source), "backup": str(target)})

    return {
        "backup_dir": str(backup_root) if copied else "",
        "files": copied,
    }


def migrate_workspace(workspace_dir: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Apply safe metadata migrations for the workspace.

    When ``dry_run`` is true, only the preflight report is returned.
    """
    workspace = _workspace(workspace_dir)
    before = inspect_workspace_migrations(str(workspace))

    if not workspace.exists():
        raise ValueError(f"工作区不存在: {workspace}")
    if not workspace.is_dir():
        raise ValueError(f"工作区不是目录: {workspace}")
    if dry_run:
        return {"dry_run": True, "migrated": False, "before": before, "after": before, "backup": {"files": []}}

    backup = _backup_existing_metadata(workspace)
    performed: list[str] = []

    if "ensure_workspace_manifest" in before["actions"]:
        ensure_workspace_manifest(str(workspace))
        performed.append("ensure_workspace_manifest")

    if "rebuild_run_index" in before["actions"]:
        rebuild_run_index(str(workspace))
        performed.append("rebuild_run_index")

    after = inspect_workspace_migrations(str(workspace))
    return {
        "dry_run": False,
        "migrated": bool(performed),
        "performed": performed,
        "before": before,
        "after": after,
        "backup": backup,
    }
