"""Context window and compaction routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.run_state import get_workspace
from src.api.services.compaction_policy_service import CompactionLevel
from src.api.services.compaction_service import compact_context_ledger
from src.api.services.context_compaction_settings_service import (
    SummaryMode,
    get_context_compaction_settings,
    save_context_compaction_settings,
)
from src.api.services.context_ledger_service import (
    ContextSection,
    build_context_ledger,
    load_latest_context_ledger,
    save_context_ledger,
)
from src.api.services.model_context_registry_service import (
    ModelContextSpec,
    get_current_model_context_spec,
    get_model_context_spec,
    list_model_context_specs,
    save_model_context_override,
)

router = APIRouter(prefix="/api/context", tags=["context"])


class ModelOverrideRequest(BaseModel):
    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    watch_ratio: float = 0.60
    soft_compact_ratio: float = 0.75
    hard_compact_ratio: float = 0.85
    emergency_ratio: float = 0.90
    last_verified: str | None = None
    notes: str = ""


class CompactRequest(BaseModel):
    level: CompactionLevel | None = None
    reason: str = "manual"
    strategy: Literal["deterministic", "summary"] = "deterministic"
    summary_mode: SummaryMode | None = None


class ContextCompactionSettingsUpdate(BaseModel):
    summary_mode: SummaryMode | None = None
    auto_compact_enabled: bool | None = None
    auto_compact_min_level: Literal["hard", "emergency"] | None = None
    manual_compact_strategy: Literal["deterministic", "summary"] | None = None


class LedgerPreviewRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    reserved_output_tokens: int | None = None
    sections: list[dict[str, Any]]
    persist: bool = False


@router.get("/models")
async def get_context_models():
    return list_model_context_specs(get_workspace())


@router.get("/model/current")
async def get_current_context_model():
    spec = get_current_model_context_spec(get_workspace())
    data = spec.model_dump()
    data["thresholds"] = spec.thresholds()
    return data


@router.put("/model/override")
async def put_context_model_override(request: ModelOverrideRequest):
    spec = save_model_context_override(ModelContextSpec(**request.model_dump(), source="override"), get_workspace())
    data = spec.model_dump()
    data["thresholds"] = spec.thresholds()
    return data


@router.get("/compaction/settings")
async def get_context_compaction_settings_route():
    return get_context_compaction_settings(get_workspace()).model_dump()


@router.put("/compaction/settings")
async def put_context_compaction_settings_route(request: ContextCompactionSettingsUpdate):
    settings = save_context_compaction_settings(request.model_dump(exclude_unset=True), get_workspace())
    return settings.model_dump()


@router.post("/ledger/preview")
async def post_context_ledger_preview(request: LedgerPreviewRequest):
    spec = (
        get_model_context_spec(request.provider, request.model, get_workspace())
        if request.provider or request.model
        else get_current_model_context_spec(get_workspace())
    )
    ledger = build_context_ledger(
        [ContextSection(**item) for item in request.sections],
        spec,
        conversation_id=request.conversation_id,
        run_id=request.run_id,
        turn_id=request.turn_id,
        reserved_output_tokens=request.reserved_output_tokens,
    )
    if request.persist:
        save_context_ledger(ledger, get_workspace())
    return ledger.model_dump()


@router.get("/runs/{run_id}/ledger")
async def get_run_context_ledger(run_id: str):
    ledger = load_latest_context_ledger(get_workspace(), run_id=run_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="Context ledger not found")
    return ledger.model_dump()


@router.get("/conversations/{conversation_id}/ledger")
async def get_conversation_context_ledger(conversation_id: str):
    ledger = load_latest_context_ledger(get_workspace(), conversation_id=conversation_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="Context ledger not found")
    return ledger.model_dump()


@router.post("/runs/{run_id}/compact")
async def post_run_context_compaction(run_id: str, request: CompactRequest | None = None):
    payload = request or CompactRequest()
    try:
        result = compact_context_ledger(
            get_workspace(),
            run_id=run_id,
            level=payload.level,
            reason=payload.reason,
            strategy=payload.strategy,
            summary_mode=payload.summary_mode or get_context_compaction_settings(get_workspace()).summary_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.model_dump()


@router.post("/conversations/{conversation_id}/compact")
async def post_conversation_context_compaction(conversation_id: str, request: CompactRequest | None = None):
    payload = request or CompactRequest()
    try:
        result = compact_context_ledger(
            get_workspace(),
            conversation_id=conversation_id,
            level=payload.level,
            reason=payload.reason,
            strategy=payload.strategy,
            summary_mode=payload.summary_mode or get_context_compaction_settings(get_workspace()).summary_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.model_dump()
