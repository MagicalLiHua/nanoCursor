"""Eval routes — list, run, history, score, suite, summary."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import get_workspace, raise_404, raise_400
from src.api.models import EvalScoreRequest, EvalSuiteRunRequest
from src.api.services.eval_service import (
    compare_eval_runs, compare_eval_runs_detailed, get_eval_artifacts,
    get_eval_run, get_eval_task,
    list_evals, run_eval, score_eval_run,
)
from src.api.services.eval_runner_service import (
    run_eval_with_command,
    run_eval_suite,
    get_eval_summary,
)

router = APIRouter(prefix="/api/evals", tags=["evals"])


@router.get("")
async def list_eval_tasks():
    return {"evals": list_evals()}


@router.get("/summary")
async def eval_summary():
    """Aggregate pass_rate and per-task stats across all eval runs."""
    return get_eval_summary(get_workspace())


@router.get("/{eval_id}")
async def get_eval_detail(eval_id: str):
    task = get_eval_task(eval_id)
    if not task:
        raise_404(f"Eval 任务不存在: {eval_id}")
    return task


@router.post("/suite/run")
async def run_eval_suite_route(request: EvalSuiteRunRequest):
    """Run multiple eval tasks as a suite.

    Request body: {"eval_ids": ["todo_web_app", "bug_fix_import_error"],
                    "mode": "agent", "stop_on_failure": false}
    """
    eval_ids = request.eval_ids
    if not eval_ids:
        raise_400("eval_ids 不能为空")
    return run_eval_suite(eval_ids, get_workspace(), request.mode, request.stop_on_failure)


@router.post("/{eval_id}/run")
async def run_eval_task(eval_id: str, mode: str = "agent"):
    """Run an eval task with test_command execution.

    Query params:
      mode: "agent" | "baseline" | "command_only" (default "agent")
    """
    from src.api.dependencies import get_event_store
    try:
        # Use real eval runner when fixture+test_command exist
        task = get_eval_task(eval_id)
        if not task:
            raise_404(f"Eval 任务不存在: {eval_id}")
        if task.get("fixture") and task.get("test_command"):
            result = run_eval_with_command(eval_id, get_workspace(), mode)
        else:
            result = run_eval(eval_id, get_workspace(), get_event_store())
        return result
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/{eval_id}/history")
async def get_eval_history(eval_id: str, limit: int = 10):
    return compare_eval_runs(eval_id, limit=min(max(limit, 1), 50))


@router.get("/runs/{eval_run_id}")
async def get_eval_run_result(eval_run_id: str):
    try:
        return get_eval_run(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))


@router.post("/runs/{eval_run_id}/score")
async def rescore_eval_run(eval_run_id: str, request: EvalScoreRequest):
    try:
        result = get_eval_run(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))
    eval_workspace = result.get("workspace_dir") or get_workspace()
    return score_eval_run(eval_run_id, eval_workspace, request.signals)


# ---- R6: Artifacts & Compare ----


@router.get("/runs/{eval_run_id}/artifacts")
async def eval_artifacts(eval_run_id: str):
    """Return all artifacts for an eval run: events, delivery, changes, failures."""
    try:
        return get_eval_artifacts(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))


@router.post("/runs/{eval_run_id}/compare")
async def eval_compare(eval_run_id: str, other_run_id: str):
    """Compare two eval runs side-by-side."""
    try:
        return compare_eval_runs_detailed(eval_run_id, other_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))
