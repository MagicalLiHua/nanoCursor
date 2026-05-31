"""Data routes: todos, memories, subagents."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.infra import db as nano_db
from src.memory.manager import get_memory_manager

router = APIRouter(prefix="/api", tags=["data"])


# --- Todos ---

@router.get("/todos")
async def list_todos():
    try:
        todos = nano_db.todo_get_all()
        return {"todos": todos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/todos")
async def create_todo(title: str, priority: int = 0, category: str | None = None):
    try:
        item = nano_db.todo_create(title=title, priority=priority, category=category)
        return {"todo": item}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/todos/{todo_id}/complete")
async def complete_todo(todo_id: str):
    result = nano_db.todo_complete(todo_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"todo": result}


@router.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str):
    deleted = nano_db.todo_delete(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"deleted": True}


# --- Memories ---

@router.get("/memories")
async def list_memories(category: str | None = None, min_importance: int = 0, limit: int = 50):
    try:
        mm = get_memory_manager()
        memories = mm.get(category=category, min_importance=min_importance, limit=limit)
        return {"memories": memories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memories")
async def create_memory(content: str, category: str, importance: int = 1, tags: str = ""):
    try:
        import json
        tag_list = json.loads(tags) if tags else []
        mm = get_memory_manager()
        entry = mm.save(category=category, content=content, importance=importance, tags=tag_list)
        return {"memory": entry}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memories/search")
async def search_memories(q: str, limit: int = 20):
    try:
        mm = get_memory_manager()
        results = mm.search(query=q, limit=limit)
        return {"memories": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, content: str | None = None, importance: int | None = None):
    result = nano_db.memory_update(memory_id, content, importance)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": result}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    deleted = nano_db.memory_delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# --- Sub-Agents ---

@router.get("/subagents")
async def list_subagents():
    try:
        active = nano_db.subagent_get_active()
        return {"active": active}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subagents/{subagent_id}")
async def get_subagent(subagent_id: str):
    result = nano_db.subagent_get(subagent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="SubAgent not found")
    return {"subagent": result}
