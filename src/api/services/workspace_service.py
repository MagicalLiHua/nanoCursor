"""Project-level overview aggregation for nanoCursor workspaces."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import os

from src.api.services.capability_service import build_capability_hub
from src.api.services.eval_service import build_aggregate_metrics
from src.api.services.conversation_service import list_conversations
from src.api.services.workspace_registry_service import get_workspace_identity
from src.api.services.recovery_service import build_recovery_center
from src.api.services.run_history import list_run_history
from src.indexer.indexer import get_project_index, reset_index
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_index_summary(workspace: Path) -> dict[str, Any]:
    try:
        reset_index()
        index = get_project_index(workspace)
        index.update()
        summary = index.summary()
        routes = index.route_summary()
        return {
            "status": "ready",
            "entry_points": summary.get("entry_points", [])[:8],
            "total_files": summary.get("total_files", 0),
            "source_count": summary.get("source_count", 0),
            "test_count": summary.get("test_count", 0),
            "config_count": summary.get("config_count", 0),
            "total_loc": summary.get("total_loc", 0),
            "recently_modified": [
                {"path": path, "mtime": mtime}
                for path, mtime in summary.get("recently_modified", [])[:6]
            ],
            "routes": routes[:12],
            "route_count": len(routes),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "entry_points": [],
            "total_files": 0,
            "source_count": 0,
            "test_count": 0,
            "config_count": 0,
            "total_loc": 0,
            "recently_modified": [],
            "routes": [],
            "route_count": 0,
        }


def build_workspace_overview(workspace_dir: str | None = None) -> dict[str, Any]:
    """Return the project-level state needed by Workspace Center."""
    workspace = _workspace(workspace_dir)
    runs = list_run_history(str(workspace), limit=8)
    conversations = list_conversations(str(workspace), limit=8)
    capabilities = build_capability_hub(str(workspace))
    recovery = build_recovery_center(None, str(workspace))
    project_index = _project_index_summary(workspace)

    configured_mcp = [
        item for item in capabilities.get("capabilities", [])
        if item.get("kind") == "mcp" and item.get("status") == "configured"
    ]
    skills = [
        item for item in capabilities.get("capabilities", [])
        if item.get("kind") == "skill"
    ]
    custom_skills = [item for item in skills if item.get("status") == "configured"]
    failed_runs = [item for item in runs if item.get("status") in {"failed", "interrupted"}]

    return {
        "workspace_dir": str(workspace),
        "generated_at": time.time(),
        "summary": {
            "conversation_count": len(conversations),
            "recent_run_count": len(runs),
            "failed_run_count": len(failed_runs),
            "skill_count": len(skills),
            "custom_skill_count": len(custom_skills),
            "configured_mcp_count": len(configured_mcp),
            "recovery_point_count": len(recovery.get("recovery_points", [])),
            "risk_count": len(recovery.get("risks", [])),
            "source_count": project_index.get("source_count", 0),
            "test_count": project_index.get("test_count", 0),
            "route_count": project_index.get("route_count", 0),
        },
        "project_index": project_index,
        "recent_conversations": conversations,
        "recent_runs": runs,
        "capability_summary": capabilities.get("summary", {}),
        "skills": skills[:8],
        "mcp": [
            item for item in capabilities.get("capabilities", [])
            if item.get("kind") == "mcp"
        ][:8],
        "recovery": {
            "status": recovery.get("status", "unknown"),
            "summary": recovery.get("summary", {}),
            "recent_points": recovery.get("recovery_points", [])[:5],
            "risks": recovery.get("risks", [])[:5],
            "actions": recovery.get("actions", [])[:4],
        },
        "health": build_workspace_health(str(workspace)),
        "aggregate_metrics": build_aggregate_metrics(str(workspace)),
    }


def build_workspace_health(workspace_dir: str | None = None) -> dict[str, Any]:
    """Return workspace health status — read-only, safe to call anytime."""
    wdir = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    identity = get_workspace_identity(str(wdir))
    exists = wdir.exists() and wdir.is_dir()
    writable = os.access(wdir, os.W_OK) if exists else False
    is_git = (wdir / ".git").exists() if exists else False

    runs_dir = wdir / ".nanocursor" / "runs"
    run_count = len([d for d in runs_dir.iterdir() if d.is_dir()]) if runs_dir.exists() else 0

    backups_dir = wdir / ".backups"
    backup_count = len([f for f in backups_dir.iterdir() if f.is_file()]) if backups_dir.exists() else 0

    settings_path = wdir / ".nanocursor" / "settings.json"
    setting_count = 1 if settings_path.exists() else 0

    return {
        "workspace_id": identity.get("workspace_id", ""),
        "path": str(wdir),
        "exists": exists,
        "writable": writable,
        "is_git_repo": is_git,
        "index_status": "indexed" if (wdir / ".nanocursor" / "project_index.json").exists() else "pending",
        "setting_count": setting_count,
        "run_count": run_count,
        "backup_count": backup_count,
    }
