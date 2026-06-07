"""Run entrypoint routes."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.models import ConversationRunRequest, RunRequest
from src.api.run_state import active_runs
from src.api.services.conversation_run_service import start_conversation_run
from src.api.services.legacy_sse_service import stream_legacy_run_events
from src.api.services.run_start_service import start_standard_run

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/run")
async def start_run(request: RunRequest):
    return await start_standard_run(request)


@router.get("/run/{thread_id}/events")
async def stream_events(thread_id: str):
    return stream_legacy_run_events(thread_id, active_runs)


@router.post("/runs")
async def start_agenthub_run(request: RunRequest):
    return await start_standard_run(request)


@router.post("/conversations/{conversation_id}/runs")
async def create_agenthub_conversation_run(conversation_id: str, request: ConversationRunRequest):
    return await start_conversation_run(conversation_id, request)
