"""Run analytics routes: diff, report, quality, score, traceability, capabilities, artifacts, outcome."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.run_state import workspace_for_thread
from src.api.services.artifact_service import build_artifact_center
from src.api.services.capability_usage_service import build_capability_usage
from src.api.services.diff_service import get_run_diff
from src.api.services.quality_service import build_quality_gate
from src.api.services.report_service import build_delivery_report
from src.api.services.run_outcome_service import build_run_outcome
from src.api.services.score_service import build_delivery_score
from src.api.services.traceability_service import build_requirement_traceability

router = APIRouter(prefix="/api/runs", tags=["run-analytics"])


@router.get("/{thread_id}/diff")
async def get_run_diff_route(thread_id: str):
    return get_run_diff(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/report")
async def get_run_report(thread_id: str):
    return build_delivery_report(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/quality")
async def get_run_quality(thread_id: str):
    return build_quality_gate(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/score")
async def get_run_score(thread_id: str):
    return build_delivery_score(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/traceability")
async def get_run_traceability(thread_id: str):
    return build_requirement_traceability(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/capabilities")
async def get_run_capabilities(thread_id: str):
    return build_capability_usage(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/artifacts")
async def get_run_artifacts(thread_id: str):
    return build_artifact_center(thread_id, workspace_for_thread(thread_id))


@router.get("/{thread_id}/outcome")
async def get_run_outcome(thread_id: str):
    return build_run_outcome(thread_id, workspace_for_thread(thread_id))
