"""Single boundary for starting the remaining legacy workflow executor."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from src.api.services.runtime_registry_service import get_run_manager


def _start_thread(run_context: Any, target: Callable[..., Any], args: tuple[Any, ...]) -> threading.Thread:
    worker = threading.Thread(target=target, args=args, daemon=True)
    run_context.thread = worker
    worker.start()
    return worker


def start_workflow_thread(
    *,
    thread_id: str,
    initial_messages: list[Any],
    workspace_dir: str,
    run_context: Any | None = None,
    workflow_runner: Callable[..., Any] | None = None,
) -> threading.Thread:
    """Start a normal/retry/remediation workflow through the compatibility adapter."""
    if workflow_runner is None:
        from src.api.runtime_facade import run_workflow

        workflow_runner = run_workflow
    context = run_context or get_run_manager().get(thread_id)
    if context is None:
        raise ValueError(f"Run 不在活跃列表中: {thread_id}")
    return _start_thread(context, workflow_runner, (thread_id, initial_messages, workspace_dir))


def start_resumed_workflow_thread(
    *,
    thread_id: str,
    messages: list[Any],
    system: str,
    workspace_dir: str,
    run_context: Any | None = None,
    workflow_runner: Callable[..., Any] | None = None,
) -> threading.Thread:
    """Resume a workflow through the compatibility adapter."""
    if workflow_runner is None:
        from src.api.runtime_facade import run_workflow_from_messages

        workflow_runner = run_workflow_from_messages
    context = run_context or get_run_manager().get(thread_id)
    if context is None:
        raise ValueError(f"Run 不在活跃列表中: {thread_id}")
    return _start_thread(context, workflow_runner, (thread_id, messages, system, workspace_dir))


__all__ = ["start_resumed_workflow_thread", "start_workflow_thread"]
