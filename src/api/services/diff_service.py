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

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "diff": diff,
        "changed_files": changed_files,
        "source": "git",
        "error": diff_result.stderr.strip() if diff_result.returncode != 0 else "",
    }
