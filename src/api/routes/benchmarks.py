"""Benchmark routes for deterministic local showcase runs."""

from __future__ import annotations

import queue
import threading
import uuid

from fastapi import APIRouter, HTTPException

from src.api.models import BenchmarkRunRequest, RealTaskBenchmarkRunRequest, RunResponse
from src.api.run_state import emit_agenthub_event, event_store, get_workspace, run_manager, runs_lock
from src.api.services.benchmark_service import (
    get_benchmark,
    get_context_window_benchmark_run,
    get_real_task_benchmark_run,
    list_context_window_benchmarks,
    list_benchmarks,
    list_real_task_benchmarks,
    run_benchmark_workflow,
    run_context_window_pressure_benchmark,
    run_real_task_benchmark_suite,
)
from src.api.services.run_context import RunContext
from src.infra.metrics import metrics as metrics_collector

router = APIRouter(prefix="/api", tags=["benchmarks"])


@router.get("/benchmarks")
async def get_agenthub_benchmarks():
    return {"benchmarks": list_benchmarks()}


@router.get("/benchmarks/real-tasks")
async def get_real_task_benchmarks():
    return {"suite": "real_tasks", "benchmarks": list_real_task_benchmarks(get_workspace())}


@router.post("/benchmarks/real-tasks/run")
async def run_real_task_benchmarks(request: RealTaskBenchmarkRunRequest):
    return run_real_task_benchmark_suite(
        request.case_ids or None,
        workspace_dir=get_workspace(),
        persist=request.persist,
    )


@router.get("/benchmarks/real-tasks/runs/{run_id}")
async def get_real_task_benchmark_result(run_id: str):
    try:
        return get_real_task_benchmark_run(run_id, get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/benchmarks/context-window")
async def get_context_window_benchmarks():
    return list_context_window_benchmarks(get_workspace())


@router.post("/benchmarks/context-window/run")
async def run_context_window_benchmark():
    return run_context_window_pressure_benchmark(workspace_dir=get_workspace(), persist=True)


@router.get("/benchmarks/context-window/runs/{run_id}")
async def get_context_window_benchmark_result(run_id: str):
    try:
        return get_context_window_benchmark_run(run_id, get_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/benchmarks/run")
async def start_agenthub_benchmark_run(request: BenchmarkRunRequest):
    try:
        benchmark = get_benchmark(request.benchmark_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    thread_id = request.thread_id or str(uuid.uuid4())
    run_workspace = request.workspace_dir or get_workspace()
    run_context = RunContext(
        thread_id=thread_id,
        workspace_dir=run_workspace,
        queue=queue.Queue(),
        status="running",
        mode="benchmark",
        team=[],
        execution_plan={},
    )

    with runs_lock:
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=thread_id,
        prompt=benchmark["prompt"],
        workspace_dir=run_workspace,
        status="running",
        mode="benchmark",
    )
    metrics_collector.reset()
    emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Benchmark Run 已启动",
        content=benchmark["prompt"],
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": "benchmark", "benchmark_id": request.benchmark_id},
        workspace_dir=run_workspace,
    )

    worker = threading.Thread(
        target=run_benchmark_workflow,
        args=(thread_id, request.benchmark_id, run_workspace),
        daemon=True,
    )
    run_context.thread = worker
    worker.start()

    return RunResponse(thread_id=thread_id, status="started")
