"""Legacy memory routes backed by governed memory."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from src.api.services.memory_governance_service import (
    create_memory_record,
    delete_memory_record,
    list_memory_records,
    update_memory_record,
)
from src.api.services.workspace_runtime_service import get_active_workspace

router = APIRouter(prefix="/api", tags=["data"])

_CATEGORY_TO_GOVERNED = {
    "user": ("global", "user_preference"),
    "feedback": ("workspace", "failure_pattern"),
    "project": ("workspace", "project_fact"),
    "reference": ("workspace", "workflow_note"),
}


def _workspace(workspace_dir: str | None) -> str:
    return workspace_dir or get_active_workspace()


def _category_for(record: dict) -> str:
    if record.get("scope") == "global" or record.get("kind") == "user_preference":
        return "user"
    if record.get("kind") == "failure_pattern":
        return "feedback"
    if record.get("kind") == "project_fact":
        return "project"
    return "reference"


def _compat_record(record: dict) -> dict:
    """Expose governed memory through the legacy response shape."""
    return {**record, "category": _category_for(record)}


@router.get("/memories")
async def list_memories(
    category: str | None = None,
    min_importance: int = 0,
    limit: int = 50,
    workspace_dir: str | None = None,
):
    memories = [
        _compat_record(item)
        for item in list_memory_records(_workspace(workspace_dir), limit=1000)
        if int(item.get("importance") or 0) >= min_importance
        and (not category or _category_for(item) == category)
    ]
    return {"memories": memories[: max(0, min(limit, 1000))]}


@router.post("/memories")
async def create_memory(
    content: str,
    category: str,
    importance: int = 1,
    tags: str = "",
    workspace_dir: str | None = None,
):
    try:
        tag_list = json.loads(tags) if tags else []
        if not isinstance(tag_list, list):
            raise ValueError("tags must be a JSON array")
        scope, kind = _CATEGORY_TO_GOVERNED.get(category, ("workspace", "workflow_note"))
        record = create_memory_record(
            _workspace(workspace_dir),
            scope=scope,
            kind=kind,
            content=content,
            source="user",
            importance=importance,
            tags=[str(tag) for tag in tag_list],
            source_ref="legacy_api:/api/memories",
            automatic=False,
        )
        return {"memory": _compat_record(record)}
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memories/search")
async def search_memories(q: str, limit: int = 20, workspace_dir: str | None = None):
    query = q.casefold().strip()
    results = []
    for item in list_memory_records(_workspace(workspace_dir), limit=1000):
        haystack = " ".join([
            str(item.get("content") or ""),
            str(item.get("summary") or ""),
            " ".join(str(tag) for tag in item.get("tags", [])),
        ]).casefold()
        if query in haystack:
            results.append(_compat_record(item))
    return {"memories": results[: max(0, min(limit, 1000))]}


@router.patch("/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    content: str | None = None,
    importance: int | None = None,
    workspace_dir: str | None = None,
):
    result = update_memory_record(
        _workspace(workspace_dir),
        memory_id,
        content=content,
        importance=importance,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": _compat_record(result)}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, workspace_dir: str | None = None):
    deleted = delete_memory_record(_workspace(workspace_dir), memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}
