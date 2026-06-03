"""Run entrypoint routes.

The implementation still lives in the legacy ``api_server`` module while the
large runtime is being modularized. Keeping the decorators here lets the public
route table live under ``src/api/routes`` without forcing a risky rewrite of the
main workflow startup path in the same step.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.api.models import ConversationRunRequest, RunRequest

router = APIRouter(prefix="/api", tags=["runs"])


def _runtime() -> Any:
    import api_server

    return api_server


@router.post("/run")
async def start_run(request: RunRequest):
    return await _runtime().start_run(request)


@router.get("/run/{thread_id}/events")
async def stream_events(thread_id: str):
    return await _runtime().stream_events(thread_id)


@router.post("/runs")
async def start_agenthub_run(request: RunRequest):
    return await _runtime().start_agenthub_run(request)


@router.post("/conversations/{conversation_id}/runs")
async def create_agenthub_conversation_run(conversation_id: str, request: ConversationRunRequest):
    return await _runtime().create_agenthub_conversation_run(conversation_id, request)
