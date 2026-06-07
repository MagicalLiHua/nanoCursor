"""Small compatibility adapter for workflow helpers still owned by legacy runtime."""

from __future__ import annotations

from typing import Any


def get_run_manager() -> Any:
    from src.api.services.runtime_registry_service import get_run_manager as _get

    return _get()


def get_active_runs() -> dict[str, Any]:
    from src.api.services.runtime_registry_service import get_active_runs as _get

    return _get()


def get_runs_lock() -> Any:
    from src.api.services.runtime_registry_service import get_runs_lock as _get

    return _get()


def get_event_store() -> Any:
    from src.api.services.runtime_registry_service import get_runtime_event_store

    return get_runtime_event_store()


def run_workflow(*args: Any, **kwargs: Any) -> Any:
    from src.api import legacy_runtime

    return legacy_runtime._run_workflow(*args, **kwargs)


def run_workflow_from_messages(*args: Any, **kwargs: Any) -> Any:
    from src.api import legacy_runtime

    return legacy_runtime._run_workflow_from_messages(*args, **kwargs)


__all__ = [
    "get_active_runs",
    "get_event_store",
    "get_run_manager",
    "get_runs_lock",
    "run_workflow",
    "run_workflow_from_messages",
]
