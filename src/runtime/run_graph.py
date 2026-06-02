"""Legacy compatibility wrapper for the Agent Loop task board.

New code should import from ``src.runtime.task_board``. These aliases keep
older tests, saved artifacts, and `/graph` API routes working while the product
language moves away from graph/DAG terminology.
"""

from __future__ import annotations

from src.runtime.task_board import (
    AcceptanceCriterion,
    NodeStatus,
    NodeType,
    QualityGate,
    ResourceLock,
    RetryPolicy,
    RunEdge,
    RunGraph,
    RunNode,
    build_run_graph,
    load_run_graph,
    save_run_graph,
)

__all__ = [
    "AcceptanceCriterion",
    "NodeStatus",
    "NodeType",
    "QualityGate",
    "ResourceLock",
    "RetryPolicy",
    "RunEdge",
    "RunGraph",
    "RunNode",
    "build_run_graph",
    "load_run_graph",
    "save_run_graph",
]
