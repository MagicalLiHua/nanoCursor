"""Diff helpers for AgentHub runs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _run_git(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _parse_status(output: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status = line[:2].strip() or "M"
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        change_type = {
            "A": "created",
            "M": "modified",
            "D": "deleted",
            "??": "created",
        }.get(status, "modified")
        files.append({"path": path, "status": status, "change_type": change_type})
    return files


def _changed_files_from_events(thread_id: str, workspace_dir: str | None = None) -> list[dict[str, Any]]:
    """Derive changed files from run events when git cannot see the workspace."""
    from src.api.services.event_store import get_event_store

    files_by_path: dict[str, dict[str, Any]] = {}
    for event in get_event_store().list_events(thread_id, str(_workspace(workspace_dir))):
        if event.type != "file_changed" or not isinstance(event.payload, dict):
            continue
        path = str(event.payload.get("path") or "").strip()
        if not path:
            continue
        output = str(event.payload.get("output") or event.content or "")
        change_type = "created" if output.startswith("Created ") else str(event.payload.get("change_type") or "modified")
        files_by_path[path] = {
            "path": path,
            "status": "event",
            "change_type": "created" if change_type == "added" else change_type,
        }
    return [files_by_path[path] for path in sorted(files_by_path)]


def get_run_diff(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Return unified diff and changed files for a run or active workspace."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(thread_id, workspace_dir)
    patch_path = run_dir / "diff.patch"
    changed_files_path = run_dir / "changed_files.json"

    if patch_path.exists():
        diff = patch_path.read_text(encoding="utf-8", errors="replace")
        changed_files = []
        if changed_files_path.exists():
            try:
                changed_files = json.loads(changed_files_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                changed_files = []
        return {
            "thread_id": thread_id,
            "workspace_dir": str(workspace),
            "diff": diff,
            "changed_files": changed_files,
            "source": "run_artifact",
        }

    diff_result = _run_git(workspace, ["diff", "--no-ext-diff", "--", "."])
    status_result = _run_git(workspace, ["status", "--short", "--", "."])

    diff = diff_result.stdout if diff_result.returncode == 0 else ""
    changed_files = _parse_status(status_result.stdout) if status_result.returncode == 0 else []
    source = "git"
    if not changed_files:
        changed_files = _changed_files_from_events(thread_id, str(workspace))
        if changed_files:
            source = "events"

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "diff": diff,
        "changed_files": changed_files,
        "source": source,
        "error": diff_result.stderr.strip() if diff_result.returncode != 0 else "",
    }
