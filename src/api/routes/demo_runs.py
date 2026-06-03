"""Deterministic demo run routes."""

from __future__ import annotations

import queue
import threading
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from src.api.models import RunRequest, RunResponse
from src.api.services.demo_run import DEMO_PROMPT, write_demo_artifacts
from src.api.services.run_context import RunContext

router = APIRouter(prefix="/api", tags=["runs"])


def _runtime() -> Any:
    """Return the legacy runtime module without importing it during app creation."""
    import api_server

    return api_server


@router.post("/runs/demo")
async def start_demo_run(request: RunRequest | None = None):
    runtime = _runtime()
    prompt = request.prompt if request and request.prompt else DEMO_PROMPT
    team = list(request.team) if request and request.team else []
    thread_id = str(uuid.uuid4())
    run_workspace = request.workspace_dir if request and request.workspace_dir else runtime._get_workspace()
    run_context = RunContext(
        thread_id=thread_id,
        workspace_dir=run_workspace,
        queue=queue.Queue(),
        status="running",
        team=team,
    )

    with runtime.runs_lock:
        try:
            runtime.run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    runtime.event_store.create_session(
        thread_id=thread_id,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
        mode="demo",
    )
    session_metadata = run_context.session_metadata()
    if session_metadata:
        runtime.event_store.update_session(thread_id, run_workspace, **session_metadata)

    runtime._emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Demo Run 已启动",
        content=prompt,
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": "demo"},
        workspace_dir=run_workspace,
    )

    artifacts = write_demo_artifacts(thread_id, run_workspace)
    worker = threading.Thread(
        target=runtime._run_demo_workflow,
        args=(thread_id, run_workspace, artifacts),
        daemon=True,
    )
    run_context.thread = worker
    worker.start()

    return RunResponse(thread_id=thread_id, status="started")
