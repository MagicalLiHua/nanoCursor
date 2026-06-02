"""Legacy compatibility wrapper for run-state services.

New code should import from ``src.api.services.run_state_service``.
"""

from __future__ import annotations

from src.api.services.run_state_service import (
    build_node_context_pack,
    build_run_context_pack,
    build_task_context_pack,
    get_node_evidence,
    get_or_create_run_state,
    get_run_task_board,
    get_task_evidence,
    patch_run_state,
    rebuild_run_state,
    refresh_summaries,
    save_run_context_pack,
    update_node_status,
)


get_or_create_run_graph = get_or_create_run_state
rebuild_run_graph = rebuild_run_state

__all__ = [
    "build_node_context_pack",
    "build_run_context_pack",
    "build_task_context_pack",
    "get_node_evidence",
    "get_or_create_run_graph",
    "get_or_create_run_state",
    "get_run_task_board",
    "get_task_evidence",
    "patch_run_state",
    "rebuild_run_graph",
    "rebuild_run_state",
    "refresh_summaries",
    "save_run_context_pack",
    "update_node_status",
]
