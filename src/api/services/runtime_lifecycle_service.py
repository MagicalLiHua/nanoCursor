"""Application startup, shutdown, recovery, and runtime-state persistence."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Callable

from src.api.services.runtime_registry_service import RuntimeRegistry, get_runtime_registry
from src.infra.logging import get_logger


_initialized = False
logger = get_logger()


def initialize_runtime_services() -> None:
    """Initialize process-wide infrastructure once.

    This remains safe to call during app construction because many existing
    tests instantiate ``TestClient`` without entering its context manager.
    """
    global _initialized
    if _initialized:
        return

    from src.api.services.sse_broker import register_event_store_push

    register_event_store_push()
    _initialized = True


def active_runs_state_path() -> Path:
    from src.infra import config as config_module

    return Path(config_module.PROJECT_ROOT) / ".nanocursor" / "active_runs_state.json"


def save_active_runs_state(
    registry: RuntimeRegistry | None = None,
    *,
    path: str | Path | None = None,
    workspace_getter: Callable[[], str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Persist a diagnostic snapshot of active in-memory runs."""
    runtime = registry or get_runtime_registry()
    state_path = Path(path) if path is not None else active_runs_state_path()
    if workspace_getter is None:
        from src.api.services.workspace_runtime_service import get_active_workspace

        workspace_getter = get_active_workspace

    with runtime.runs_lock:
        snapshot = {
            thread_id: {
                "thread_id": thread_id,
                "workspace_dir": context.get("workspace_dir", workspace_getter()),
                "status": context.get("status", "unknown"),
                "conversation_id": context.get("conversation_id", ""),
                "started_at": getattr(context, "started_at", time.time()),
                "mode": context.get("mode", "agenthub_delivery"),
            }
            for thread_id, context in runtime.active_runs.items()
        }

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning(
            "active_runs_state_persist_failed",
            extra={"path": str(state_path)},
            exc_info=True,
        )
    return snapshot


def recover_interrupted_runs(
    registry: RuntimeRegistry | None = None,
    *,
    workspace_dir: str | None = None,
) -> list[str]:
    """Mark abandoned running sessions as interrupted and emit recovery events."""
    runtime = registry or get_runtime_registry()
    if workspace_dir is None:
        from src.api.services.workspace_runtime_service import get_active_workspace

        workspace_dir = get_active_workspace()

    recovered = runtime.run_manager.detect_interrupted(workspace_dir)
    for thread_id in recovered:
        runtime.event_store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="运行中断",
            content="服务在运行期间关闭。该运行已标记为 interrupted，可重新启动。",
            agent="system",
            payload={"reason": "server_shutdown"},
            workspace_dir=workspace_dir,
        )
    return recovered


async def cleanup_runtime_periodically(
    registry: RuntimeRegistry | None = None,
    *,
    interval_seconds: float = 600,
    older_than_hours: int = 24,
) -> None:
    """Periodically release stale terminal runs from the shared registry."""
    runtime = registry or get_runtime_registry()
    from src.api.services.run_lifecycle_service import cleanup_stale_runs
    from src.api.services.workspace_runtime_service import get_active_workspace

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            workspace_dir = get_active_workspace()
            cleanup_stale_runs(
                runtime.run_manager,
                workspace_dir,
                older_than_hours=older_than_hours,
            )
        except Exception:
            logger.warning(
                "runtime_cleanup_failed",
                extra={"workspace_id": locals().get("workspace_dir", "")},
                exc_info=True,
            )
            continue


@asynccontextmanager
async def runtime_lifespan(_app):
    """Shared FastAPI lifespan used by both official and compatibility apps."""
    initialize_runtime_services()
    recover_interrupted_runs()
    cleanup_task = asyncio.create_task(cleanup_runtime_periodically())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        save_active_runs_state()


__all__ = [
    "active_runs_state_path",
    "cleanup_runtime_periodically",
    "initialize_runtime_services",
    "recover_interrupted_runs",
    "runtime_lifespan",
    "save_active_runs_state",
]
