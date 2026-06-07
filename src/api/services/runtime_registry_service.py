"""Process-wide runtime registry shared by every API entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.api.services.event_store import EventStore, get_event_store
from src.runtime.run_manager import RunManager


@dataclass(frozen=True)
class RuntimeRegistry:
    """Own the process-wide in-memory run state and durable event store."""

    run_manager: RunManager
    event_store: EventStore

    @property
    def active_runs(self) -> dict[str, Any]:
        return self.run_manager._active

    @property
    def runs_lock(self) -> Any:
        return self.run_manager._lock


_registry = RuntimeRegistry(
    run_manager=RunManager(),
    event_store=get_event_store(),
)


def get_runtime_registry() -> RuntimeRegistry:
    return _registry


def get_run_manager() -> RunManager:
    return _registry.run_manager


def get_active_runs() -> dict[str, Any]:
    return _registry.active_runs


def get_runs_lock() -> Any:
    return _registry.runs_lock


def get_runtime_event_store() -> EventStore:
    return _registry.event_store


__all__ = [
    "RuntimeRegistry",
    "get_runtime_registry",
    "get_run_manager",
    "get_active_runs",
    "get_runs_lock",
    "get_runtime_event_store",
]
