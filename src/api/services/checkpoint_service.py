"""File checkpoint system: snapshot files before modification for safe rollback."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.infra.path_guard import resolve_workspace_path


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _checkpoints_root(workspace: Path, thread_id: str) -> Path:
    root = workspace / ".checkpoints" / thread_id.replace("/", "_").replace("\\", "_")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename(filepath: str) -> str:
    return filepath.replace("/", "_").replace("\\", "_").replace("..", "_")


def create_checkpoint(
    filepath: str,
    reason: str = "",
    stage_id: str = "",
    thread_id: str = "",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Create a checkpoint of a file before modification."""
    workspace = _workspace(workspace_dir)
    source = resolve_workspace_path(workspace, filepath)
    if not source.exists():
        raise ValueError(f"文件不存在: {filepath}")

    root = _checkpoints_root(workspace, thread_id)
    rel_path = str(source.relative_to(workspace))
    safe_name = _safe_filename(rel_path)
    ts = int(time.time() * 1000)
    checkpoint_name = f"{safe_name}.{ts}"
    dest = root / checkpoint_name
    shutil.copy2(source, dest)

    meta = {
        "checkpoint_id": checkpoint_name,
        "filepath": rel_path,
        "thread_id": thread_id,
        "reason": reason,
        "stage_id": stage_id,
        "created_at": time.time(),
        "size": source.stat().st_size,
    }
    meta_path = root / f"{checkpoint_name}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return meta


def list_checkpoints(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """List all checkpoints for a run, grouped by file."""
    workspace = _workspace(workspace_dir)
    root = _checkpoints_root(workspace, thread_id)
    if not root.exists():
        return {"thread_id": thread_id, "checkpoints": [], "files": {}}

    checkpoints: list[dict[str, Any]] = []
    for meta_file in sorted(root.glob("*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            checkpoints.append(meta)
        except (json.JSONDecodeError, OSError):
            continue

    # Group by file
    by_file: dict[str, list[dict[str, Any]]] = {}
    for cp in checkpoints:
        fp = cp.get("filepath", "unknown")
        by_file.setdefault(fp, []).append(cp)

    return {
        "thread_id": thread_id,
        "checkpoints": checkpoints,
        "files": by_file,
        "total": len(checkpoints),
    }


def restore_checkpoint(
    checkpoint_id: str,
    thread_id: str,
    confirmed: bool = False,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Restore a file from a checkpoint."""
    if not confirmed:
        raise ValueError("restore_checkpoint 需要 confirmed=true 确认。")

    workspace = _workspace(workspace_dir)
    root = _checkpoints_root(workspace, thread_id)
    meta_path = root / f"{checkpoint_id}.meta.json"
    if not meta_path.exists():
        raise ValueError(f"Checkpoint 不存在: {checkpoint_id}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    source = root / checkpoint_id
    dest = resolve_workspace_path(workspace, meta["filepath"])

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)

    return {
        "restored": True,
        "checkpoint_id": checkpoint_id,
        "filepath": meta["filepath"],
        "message": f"已将 {meta['filepath']} 恢复到 checkpoint {checkpoint_id}",
    }
