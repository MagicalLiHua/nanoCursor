"""Governed memory CRUD, freshness, extraction, and preview routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.services.memory_governance_service import (
    create_memory_record,
    delete_memory_record,
    extract_run_memory,
    get_memory_record,
    list_memory_records,
    refresh_memory_freshness,
    update_memory_record,
)
from src.api.services.memory_selection_service import select_memories
from src.api.services.workspace_runtime_service import get_active_workspace


router = APIRouter(prefix="/api", tags=["memory"])


class MemoryCreateRequest(BaseModel):
    workspace_dir: str | None = None
    scope: str = "workspace"
    kind: str = "workflow_note"
    content: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "user"
    conversation_id: str | None = None
    run_id: str | None = None
    file_path: str | None = None
    source_ref: str | None = None
    confidence: float = 0.7
    importance: int = 5
    evidence_refs: list[str] = Field(default_factory=list)


class MemoryUpdateRequest(BaseModel):
    workspace_dir: str | None = None
    content: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    confidence: float | None = None
    importance: int | None = None
    status: str | None = None
    expires_at: float | None = None
    evidence_refs: list[str] | None = None


class MemoryPreviewRequest(BaseModel):
    workspace_dir: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    prompt: str = ""
    selected_files: list[str] = Field(default_factory=list)
    active_task: dict[str, Any] = Field(default_factory=dict)
    budget: int = Field(default=1200, ge=100, le=12000)


def _workspace(workspace_dir: str | None) -> str:
    return workspace_dir or get_active_workspace()


@router.get("/memory")
async def list_governed_memory(
    workspace_dir: str | None = None,
    scope: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    limit: int = 200,
):
    return {
        "memories": list_memory_records(
            _workspace(workspace_dir),
            scope=scope,
            conversation_id=conversation_id,
            run_id=run_id,
            status=status,
            include_deleted=include_deleted,
            limit=limit,
        )
    }


@router.get("/memory/{memory_id}")
async def get_governed_memory(memory_id: str, workspace_dir: str | None = None):
    record = get_memory_record(_workspace(workspace_dir), memory_id)
    if not record:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": record}


@router.post("/memory")
async def create_governed_memory(request: MemoryCreateRequest):
    try:
        record = create_memory_record(
            _workspace(request.workspace_dir),
            scope=request.scope,
            kind=request.kind,
            content=request.content,
            summary=request.summary,
            tags=request.tags,
            source=request.source,
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            file_path=request.file_path,
            source_ref=request.source_ref,
            confidence=request.confidence,
            importance=request.importance,
            evidence_refs=request.evidence_refs,
            automatic=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"memory": record}


@router.patch("/memory/{memory_id}")
async def update_governed_memory(memory_id: str, request: MemoryUpdateRequest):
    try:
        record = update_memory_record(
            _workspace(request.workspace_dir),
            memory_id,
            **request.model_dump(exclude={"workspace_dir"}, exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": record}


@router.delete("/memory/{memory_id}")
async def delete_governed_memory(memory_id: str, workspace_dir: str | None = None):
    if not delete_memory_record(_workspace(workspace_dir), memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


@router.get("/workspace/memory")
async def get_workspace_memory(workspace_dir: str | None = None, include_deleted: bool = False):
    workspace = _workspace(workspace_dir)
    return {
        "workspace_dir": workspace,
        "memories": list_memory_records(workspace, include_deleted=include_deleted, limit=1000),
    }


@router.post("/workspace/memory/refresh")
async def refresh_workspace_memory(workspace_dir: str | None = None):
    return refresh_memory_freshness(_workspace(workspace_dir))


@router.get("/runs/{thread_id}/memory")
async def get_run_memory(thread_id: str, workspace_dir: str | None = None):
    return {
        "thread_id": thread_id,
        "memories": list_memory_records(_workspace(workspace_dir), scope="run", run_id=thread_id),
    }


@router.post("/runs/{thread_id}/memory/extract")
async def extract_run_memory_route(thread_id: str, workspace_dir: str | None = None):
    try:
        return extract_run_memory(_workspace(workspace_dir), thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/context/memory/preview")
async def preview_context_memory(request: MemoryPreviewRequest):
    return select_memories(
        _workspace(request.workspace_dir),
        prompt=request.prompt,
        conversation_id=request.conversation_id,
        run_id=request.run_id,
        selected_files=request.selected_files,
        active_task=request.active_task,
        budget_tokens=request.budget,
        persist_audit=False,
    )

