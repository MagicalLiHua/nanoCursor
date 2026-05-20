"""Build structured ContextPack from workspace state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.context_pack import ContextPack
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def build_context_pack(
    prompt: str = "",
    team: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
    execution_plan: dict[str, Any] | None = None,
) -> ContextPack:
    """Build a structured context pack from workspace and execution state."""
    workspace = _workspace(workspace_dir)
    pack = ContextPack()

    # Task summary
    pack.task_summary = (prompt or "")[:200]

    # Workspace summary
    pack.workspace_summary = _workspace_summary(workspace)

    # Relevant files from project index
    index_data = _read_project_index(workspace)
    pack.relevant_files = list(index_data.get("entry_points", [])[:8])
    pack.symbols = list(index_data.get("entry_points", [])[:10])

    # Recent failures from recovery
    from src.api.services.recovery_service import build_recovery_center
    recovery = build_recovery_center(None, str(workspace))
    pack.recent_failures = [
        {
            "category": r.get("evidence", {}).get("failure_category", "unknown"),
            "summary": r.get("title", ""),
            "detail": r.get("detail", ""),
        }
        for r in recovery.get("risks", [])[:5]
    ]

    # User preferences
    try:
        from src.api.services.preference_service import build_memory_profile
        profile = build_memory_profile(str(workspace))
        pack.user_preferences = [
            b.get("label", "") for b in profile.get("buckets", [])[:3]
            if b.get("label")
        ]
    except Exception:
        pack.user_preferences = []

    # Selected skills
    if execution_plan:
        capabilities = execution_plan.get("capabilities", []) or []
        pack.selected_skills = [
            c for c in capabilities if isinstance(c, str) and c.startswith("skill.")
        ]

    # Token budget
    pack.token_budget = {
        "max_tokens": 12000,
        "used_tokens_estimate": pack.estimate_tokens(),
    }

    return pack


def _workspace_summary(workspace: Path) -> dict[str, Any]:
    """Minimal workspace summary without heavy imports."""
    summary: dict[str, Any] = {"path": str(workspace)}
    try:
        from src.api.services.workspace_service import build_workspace_health
        health = build_workspace_health(str(workspace))
        summary.update(health)
    except Exception:
        pass
    return summary


def _read_project_index(workspace: Path) -> dict[str, Any]:
    import json
    idx_path = workspace / ".nanocursor" / "project_index.json"
    if idx_path.exists():
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}
