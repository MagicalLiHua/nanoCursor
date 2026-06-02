"""Conversation routes for workspace-scoped nanoCursor sessions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.models import (
    ConversationCreateRequest,
    ConversationTeamRecommendRequest,
    ConversationTeamUpdateRequest,
)
from src.api.services.conversation_service import (
    create_conversation,
    get_conversation,
    get_conversation_memory,
    list_conversation_runs,
    list_conversations,
    refresh_conversation_memory,
    refresh_conversation_recommendation,
    update_conversation_team,
)
from src.api.services.workspace_runtime_service import get_active_workspace


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("")
async def create_agenthub_conversation(request: ConversationCreateRequest):
    return {
        "conversation": create_conversation(
            prompt=request.prompt,
            workspace_dir=request.workspace_dir or get_active_workspace(),
        )
    }


@router.get("")
async def list_agenthub_conversations(limit: int = 50, workspace_dir: str | None = None):
    safe_limit = min(max(limit, 0), 200)
    return {
        "conversations": list_conversations(
            limit=safe_limit,
            workspace_dir=workspace_dir or get_active_workspace(),
        )
    }


@router.get("/{conversation_id}")
async def get_agenthub_conversation(conversation_id: str, workspace_dir: str | None = None):
    conv = get_conversation(conversation_id, workspace_dir or get_active_workspace())
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.get("/{conversation_id}/runs")
async def list_agenthub_conversation_runs(
    conversation_id: str,
    limit: int = 50,
    workspace_dir: str | None = None,
):
    runs = list_conversation_runs(
        conversation_id,
        workspace_dir=workspace_dir or get_active_workspace(),
        limit=limit,
    )
    if not runs:
        raise HTTPException(status_code=404, detail="会话不存在")
    return runs


@router.get("/{conversation_id}/memory")
async def get_agenthub_conversation_memory(conversation_id: str, workspace_dir: str | None = None):
    try:
        return get_conversation_memory(conversation_id, workspace_dir or get_active_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{conversation_id}/memory/refresh")
async def refresh_agenthub_conversation_memory(conversation_id: str, workspace_dir: str | None = None):
    try:
        conversation = refresh_conversation_memory(conversation_id, workspace_dir or get_active_workspace())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "conversation_id": conversation_id,
        "conversation_summary": conversation.get("conversation_summary", ""),
        "conversation_memory": conversation.get("conversation_memory", {}),
        "summary_stats": conversation.get("summary_stats", {}),
    }


@router.post("/{conversation_id}/team/recommend")
async def recommend_agenthub_conversation_team(
    conversation_id: str,
    request: ConversationTeamRecommendRequest | None = None,
):
    prompt = request.prompt if request else ""
    workspace_dir = request.workspace_dir if request and request.workspace_dir else get_active_workspace()
    try:
        recommendation = refresh_conversation_recommendation(conversation_id, prompt, workspace_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "conversation_id": conversation_id,
        "recommendation": recommendation,
    }


@router.put("/{conversation_id}/team")
async def update_agenthub_conversation_team(conversation_id: str, request: ConversationTeamUpdateRequest):
    try:
        team = update_conversation_team(
            conversation_id=conversation_id,
            members=request.members,
            workspace_dir=request.workspace_dir or get_active_workspace(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"team": team}
