"""Workspace runtime service — active workspace get/set, thread-workspace mapping.

All workspace path mutations flow through this service so route modules never
touch config_module.WORKSPACE_DIR directly.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_active_workspace() -> str:
    """Return the currently active workspace directory."""
    import src.infra.config as config_module
    return config_module.WORKSPACE_DIR


def set_active_workspace(dir_path: str) -> str:
    """Switch the active workspace and reset workspace-scoped caches.

    Returns the resolved absolute path.
    """
    import src.infra.config as config_module

    abs_path = os.path.abspath(os.path.expanduser(dir_path))
    os.makedirs(abs_path, exist_ok=True)
    config_module.WORKSPACE_DIR = abs_path

    # Reset file-tools workspace
    try:
        import src.tools.file_tools as ft
        ft.WORKSPACE_DIR = abs_path
        ft.BACKUP_DIR = os.path.join(abs_path, ".backups")
        os.makedirs(ft.BACKUP_DIR, exist_ok=True)
    except Exception:
        pass

    # Reset project indexer
    try:
        from src.indexer.indexer import reset_index
        reset_index()
    except Exception:
        pass

    # Reset git workspace
    try:
        from src.tools.git_tools import set_git_workspace
        set_git_workspace(Path(abs_path))
    except Exception:
        pass

    # Reset engine runtime caches
    try:
        from src.agent.engine import reset_runtime_caches
        reset_runtime_caches()
    except Exception:
        pass

    return abs_path


def workspace_for_thread(thread_id: str, active_runs: dict, lock) -> str:
    """Return the workspace directory associated with *thread_id*.

    Falls back to the active workspace if no run is registered.
    """
    with lock:
        run_info = active_runs.get(thread_id)
        workspace_dir = run_info.get("workspace_dir") if run_info else None
    return workspace_dir or get_active_workspace()
