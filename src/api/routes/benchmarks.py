"""Benchmark routes for deterministic local showcase runs."""

from __future__ import annotations

import queue
import threading
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from src.api.models import BenchmarkRunRequest, RunResponse
from src.api.services.benchmark_service import emit_benchmark_run, get_benchmark, list_benchmarks
from src.api.services.run_context import RunContext

router = APIRouter(prefix="/api", tags=["benchmarks"])


def _runtime() -> Any:
    """Return the legacy runtime module without importing it during app creation."""
    import api_server

    return api_server


def _finish_benchmark_run(thread_id: str, workspace_dir: str, final_status: str) -> None:
    runtime = _runtime()
    with runtime.runs_lock:
        run_info = runtime.active_runs.get(thread_id)
        if run_info:
            run_info.finalize_lifecycle(final_status)
            run_info.set_status(final_status)
            runtime.event_store.update_session(
                thread_id,
                workspace_dir,
                status=final_status,
                **run_info.session_metadata(),
            )
    try:
        from src.api.services.delivery_service import finalize_delivery

        finalize_delivery(thread_id, workspace_dir)
    except Exception:
        pass
    try:
        runtime.run_manager.finalize(thread_id, final_status)
    finally:
        runtime.run_manager.unregister(thread_id)


def _run_benchmark_worker(thread_id: str, benchmark_id: str, workspace_dir: str) -> None:
    runtime = _runtime()
    final_status = "completed"

    def update_status(status: str) -> None:
        nonlocal final_status
        final_status = status
        with runtime.runs_lock:
            run_info = runtime.active_runs.get(thread_id)
            if run_info:
                run_info.set_status(status)

    try:
        emit_benchmark_run(
            thread_id=thread_id,
            benchmark_id=benchmark_id,
            workspace_dir=workspace_dir,
            store=runtime.event_store,
            status_callback=update_status,
        )
    except Exception as exc:
        final_status = "failed"
        runtime.event_store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="Benchmark Run 异常",
            content=str(exc),
            agent="lead",
            payload={"error": str(exc), "benchmark_id": benchmark_id},
            workspace_dir=workspace_dir,
        )
        runtime.event_store.update_session(thread_id, workspace_dir, status="failed", error=str(exc))
    finally:
        _finish_benchmark_run(thread_id, workspace_dir, final_status)


@router.get("/benchmarks")
async def get_agenthub_benchmarks():
    return {"benchmarks": list_benchmarks()}


@router.post("/benchmarks/run")
async def start_agenthub_benchmark_run(request: BenchmarkRunRequest):
    try:
        benchmark = get_benchmark(request.benchmark_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime = _runtime()
    thread_id = request.thread_id or str(uuid.uuid4())
    run_workspace = request.workspace_dir or runtime._get_workspace()
    run_context = RunContext(
        thread_id=thread_id,
        workspace_dir=run_workspace,
        queue=queue.Queue(),
        status="running",
        mode="benchmark",
        team=[],
        execution_plan={},
    )

    with runtime.runs_lock:
        try:
            runtime.run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    runtime.event_store.create_session(
        thread_id=thread_id,
        prompt=benchmark["prompt"],
        workspace_dir=run_workspace,
        status="running",
        mode="benchmark",
    )
    runtime.metrics_collector.reset()
    runtime._emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Benchmark Run 已启动",
        content=benchmark["prompt"],
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": "benchmark", "benchmark_id": request.benchmark_id},
        workspace_dir=run_workspace,
    )

    worker = threading.Thread(
        target=_run_benchmark_worker,
        args=(thread_id, request.benchmark_id, run_workspace),
        daemon=True,
    )
    run_context.thread = worker
    worker.start()

    return RunResponse(thread_id=thread_id, status="started")
