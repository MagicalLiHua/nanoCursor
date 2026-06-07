"""Deterministic demo run routes."""

from __future__ import annotations

import queue
import threading
import uuid

from fastapi import APIRouter, HTTPException

from src.api.models import RunRequest, RunResponse
from src.api.run_state import (
    emit_agenthub_event,
    event_store,
    get_workspace,
    run_manager,
    runs_lock,
)
from src.api.services.demo_run import DEMO_PROMPT, run_demo_workflow, write_demo_artifacts
from src.api.services.run_context import RunContext

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/runs/demo")
async def start_demo_run(request: RunRequest | None = None):
    prompt = request.prompt if request and request.prompt else DEMO_PROMPT
    team = list(request.team) if request and request.team else []
    thread_id = str(uuid.uuid4())
    run_workspace = request.workspace_dir if request and request.workspace_dir else get_workspace()
    run_context = RunContext(
        thread_id=thread_id,
        workspace_dir=run_workspace,
        queue=queue.Queue(),
        status="running",
        team=team,
    )

    with runs_lock:
        try:
            run_manager.register(run_context)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    event_store.create_session(
        thread_id=thread_id,
        prompt=prompt,
        workspace_dir=run_workspace,
        status="running",
        mode="demo",
    )
    session_metadata = run_context.session_metadata()
    if session_metadata:
        event_store.update_session(thread_id, run_workspace, **session_metadata)

    emit_agenthub_event(
        thread_id=thread_id,
        event_type="run_started",
        title="Demo Run 已启动",
        content=prompt,
        payload={"workspace_dir": run_workspace, "thread_id": thread_id, "mode": "demo"},
        workspace_dir=run_workspace,
    )

    artifacts = write_demo_artifacts(thread_id, run_workspace)
    worker = threading.Thread(
        target=run_demo_workflow,
        args=(thread_id, run_workspace, artifacts),
        daemon=True,
    )
    run_context.thread = worker
    worker.start()

    return RunResponse(thread_id=thread_id, status="started")
