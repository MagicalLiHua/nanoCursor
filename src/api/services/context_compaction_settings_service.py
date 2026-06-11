"""Workspace-scoped context compaction settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.infra import config as config_module

SummaryMode = Literal["deterministic", "llm"]
AutoCompactMinLevel = Literal["hard", "emergency"]
ManualCompactStrategy = Literal["deterministic", "summary"]


class ContextCompactionSettings(BaseModel):
    summary_mode: SummaryMode = "deterministic"
    auto_compact_enabled: bool = True
    auto_compact_min_level: AutoCompactMinLevel = "hard"
    manual_compact_strategy: ManualCompactStrategy = "summary"
    updated_at: float | None = Field(default=None, ge=0)


def get_context_compaction_settings(workspace_dir: str | Path | None = None) -> ContextCompactionSettings:
    path = _settings_path(workspace_dir)
    if not path.exists():
        return ContextCompactionSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ContextCompactionSettings()
    if not isinstance(data, dict):
        return ContextCompactionSettings()
    try:
        return ContextCompactionSettings(**data)
    except ValueError:
        return ContextCompactionSettings()


def save_context_compaction_settings(
    settings: dict[str, Any] | ContextCompactionSettings,
    workspace_dir: str | Path | None = None,
) -> ContextCompactionSettings:
    current = get_context_compaction_settings(workspace_dir)
    incoming = settings.model_dump(exclude_unset=True) if isinstance(settings, ContextCompactionSettings) else settings
    merged = current.model_dump()
    merged.update({key: value for key, value in (incoming or {}).items() if value is not None})
    updated = ContextCompactionSettings(**merged)
    path = _settings_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return updated


def should_auto_compact_level(level: str, settings: ContextCompactionSettings) -> bool:
    if not settings.auto_compact_enabled:
        return False
    if settings.auto_compact_min_level == "emergency":
        return level == "emergency"
    return level in {"hard", "emergency"}


def _settings_path(workspace_dir: str | Path | None = None) -> Path:
    workspace = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    return workspace / ".nanocursor" / "context" / "compaction_settings.json"
