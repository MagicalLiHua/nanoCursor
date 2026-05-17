"""Run history summaries for the AgentHub workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _runs_root(workspace_dir: str | None = None) -> Path:
    return _workspace(workspace_dir) / ".nanocursor" / "runs"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _count_events(path: Path) -> tuple[int, str | None]:
    if not path.exists():
        return 0, None

    count = 0
    last_event_type = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, None

    for line in lines:
        if not line.strip():
            continue
        count += 1
        try:
            event = json.loads(line)
            last_event_type = event.get("type") or last_event_type
        except json.JSONDecodeError:
            continue
    return count, last_event_type


def _changed_files_count(path: Path) -> int:
    data = _read_json(path)
    return len(data) if isinstance(data, list) else 0


def list_run_history(
    workspace_dir: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return run sessions with lightweight artifact metadata."""
    root = _runs_root(workspace_dir)
    if not root.exists():
        return []

    runs: list[dict[str, Any]] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue

        session = _read_json(run_dir / "session.json")
        if not isinstance(session, dict):
            continue

        if status and session.get("status") != status:
            continue
        if mode and session.get("mode") != mode:
            continue

        event_count, last_event_type = _count_events(run_dir / "events.jsonl")
        changed_files_count = _changed_files_count(run_dir / "changed_files.json")

        runs.append(
            {
                "thread_id": session.get("thread_id") or run_dir.name,
                "workspace_dir": session.get("workspace_dir") or str(_workspace(workspace_dir)),
                "status": session.get("status") or "unknown",
                "prompt": session.get("prompt") or "",
                "mode": session.get("mode") or "agenthub_delivery",
                "created_at": session.get("created_at"),
                "updated_at": session.get("updated_at"),
                "event_count": event_count,
                "changed_files_count": changed_files_count,
                "has_diff": (run_dir / "diff.patch").exists(),
                "has_report": (run_dir / "report.md").exists(),
                "last_event_type": last_event_type,
            }
        )

    runs.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    return runs[: max(limit, 0)]
