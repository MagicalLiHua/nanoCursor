"""Eval routes — list, run, history, score, suite, summary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.api.dependencies import get_workspace, raise_400, raise_404
from src.api.models import (
    AgentEvalRunRequest,
    AgentLoopEvalRunRequest,
    EvalScoreRequest,
    EvalSuiteRunRequest,
    IntentEvalRunRequest,
    RoutingEvalRunRequest,
)
from src.api.services.ablation_benchmark_service import (
    build_ablation_matrix,
    build_component_necessity_report,
    create_ablation_suite,
    get_ablation_artifacts,
    get_ablation_report,
    get_ablation_suite,
    list_ablation_components,
    list_ablation_suites,
    run_ablation_suite,
    run_persisted_ablation_suite,
    save_ablation_artifacts,
)
from src.api.services.agent_eval_service import (
    get_agent_eval_run,
    list_agent_eval_catalog,
    list_agent_eval_runs,
    run_agent_eval_suite,
    summarize_agent_eval_runs,
)
from src.api.services.agent_loop_eval_service import (
    get_agent_loop_eval_run,
    list_agent_loop_eval_cases,
    run_agent_loop_eval_suite,
)
from src.api.services.eval_runner_service import (
    get_eval_summary,
    run_eval_suite,
    run_eval_with_command,
)
from src.api.services.eval_service import (
    compare_eval_runs,
    compare_eval_runs_detailed,
    get_eval_artifacts,
    get_eval_run,
    get_eval_task,
    list_evals,
    run_eval,
    score_eval_run,
)
from src.api.services.intent_eval_service import (
    get_intent_eval_run,
    list_intent_eval_cases,
    run_intent_eval_suite,
)
from src.api.services.routing_eval_service import (
    get_routing_eval_run,
    list_routing_eval_cases,
    run_routing_eval_suite,
)
from src.api.services.run_eval_metrics_service import (
    build_run_eval_metrics,
    build_workspace_eval_metrics,
)

router = APIRouter(prefix="/api/evals", tags=["evals"])


class AblationMatrixRequest(BaseModel):
    eval_ids: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    include_baseline: bool = True
    repetitions: int = Field(default=1, ge=1, le=20)
    mode: str = "deterministic"


class AblationReportRequest(BaseModel):
    suite: dict[str, Any] = Field(default_factory=dict)
    matrix: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    persist: bool = False


class AblationSuiteRunRequest(BaseModel):
    eval_ids: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    repetitions: int = Field(default=1, ge=1, le=10)
    mode: str = "deterministic"
    persist: bool = True


class AblationSuiteCreateRequest(BaseModel):
    eval_ids: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    repetitions: int = Field(default=1, ge=1, le=20)
    mode: str = "deterministic"


@router.get("")
async def list_eval_tasks():
    return {"evals": list_evals()}


@router.get("/summary")
async def eval_summary():
    """Aggregate pass_rate and per-task stats across all eval runs."""
    return get_eval_summary(get_workspace())


@router.get("/intent/catalog")
async def list_intent_evals():
    """Return the Intent Router V3 core eval catalog."""
    return {"suite": "intent_core", "cases": list_intent_eval_cases()}


@router.get("/intent/cases")
async def list_intent_eval_cases_route():
    """Return Intent Router eval cases using the canonical plan endpoint."""
    return {"suite": "intent_core", "cases": list_intent_eval_cases()}


@router.post("/intent/run")
async def run_intent_evals(request: IntentEvalRunRequest):
    """Run Intent Router V3 core routing evals."""
    return run_intent_eval_suite(
        request.case_ids or None,
        workspace_dir=get_workspace(),
        persist=request.persist,
    )


@router.get("/intent/runs/{eval_run_id}")
async def get_intent_eval_result(eval_run_id: str):
    try:
        return get_intent_eval_run(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/routing/catalog")
async def list_routing_evals():
    """Return the Routing Decision core eval catalog."""
    return {"suite": "routing_core", "cases": list_routing_eval_cases()}


@router.post("/routing/run")
async def run_routing_evals(request: RoutingEvalRunRequest):
    """Run Routing Decision core evals."""
    return run_routing_eval_suite(
        request.case_ids or None,
        workspace_dir=get_workspace(),
        persist=request.persist,
    )


@router.get("/routing/runs/{eval_run_id}")
async def get_routing_eval_result(eval_run_id: str):
    try:
        return get_routing_eval_run(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/agent-loop/catalog")
async def list_agent_loop_evals():
    """Return Agent Loop controller eval catalog."""
    return {"suite": "agent_loop_core", "cases": list_agent_loop_eval_cases()}


@router.post("/agent-loop/run")
async def run_agent_loop_evals(request: AgentLoopEvalRunRequest):
    """Run Agent Loop controller evals."""
    return run_agent_loop_eval_suite(
        request.case_ids or None,
        workspace_dir=get_workspace(),
        persist=request.persist,
    )


@router.get("/agent-loop/runs/{eval_run_id}")
async def get_agent_loop_eval_result(eval_run_id: str):
    try:
        return get_agent_loop_eval_run(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/agent/catalog")
async def list_agent_evals():
    """Return aggregate agent-runtime eval catalog."""
    return list_agent_eval_catalog()


@router.post("/agent/run")
async def run_agent_evals(request: AgentEvalRunRequest):
    """Run aggregate agent-runtime evals."""
    return run_agent_eval_suite(
        request.suite,
        workspace_dir=get_workspace(),
        persist=request.persist,
        task_eval_ids=request.task_eval_ids or None,
    )


@router.get("/agent/summary")
async def agent_eval_summary(limit: int = 20):
    """Return aggregate agent eval trend summary."""
    return summarize_agent_eval_runs(get_workspace(), limit=min(max(limit, 1), 100))


@router.get("/agent/runs")
async def list_agent_eval_results(limit: int = 20):
    """List recent aggregate agent eval runs."""
    return list_agent_eval_runs(get_workspace(), limit=min(max(limit, 1), 100))


@router.get("/agent/runs/{eval_run_id}")
async def get_agent_eval_result(eval_run_id: str):
    try:
        return get_agent_eval_run(eval_run_id, get_workspace())
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/runtime/summary")
async def runtime_eval_summary(limit: int = 50):
    """Return evidence-based quality trends for recent workspace runs."""
    return build_workspace_eval_metrics(get_workspace(), limit=min(max(limit, 1), 200))


@router.get("/runtime/runs/{thread_id}/metrics")
async def runtime_run_eval_metrics(thread_id: str):
    """Return explainable loop/context/tool/recovery metrics for one run."""
    result = build_run_eval_metrics(thread_id, get_workspace())
    if result.get("status") == "not_found":
        raise_404(f"Run 不存在: {thread_id}")
    return result


@router.get("/ablation/components")
async def ablation_components():
    """Return supported components for ablation benchmarks."""
    return {"components": list_ablation_components()}


@router.post("/ablation/matrix")
async def ablation_matrix(request: AblationMatrixRequest):
    """Build baseline + single-component-disable ablation matrix."""
    try:
        return build_ablation_matrix(
            request.eval_ids,
            request.components,
            include_baseline=request.include_baseline,
            repetitions=request.repetitions,
            mode=request.mode,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise_400(str(exc))


@router.post("/ablation/report")
async def ablation_report(request: AblationReportRequest):
    """Score component contribution from completed ablation results."""
    suite_result = {
        "suite": request.suite,
        "matrix": request.matrix,
        "results": request.results,
    }
    if request.persist:
        return save_ablation_artifacts(get_workspace(), suite_result)
    return build_component_necessity_report(suite_result)


@router.post("/ablation/suite/run")
async def ablation_suite_run(request: AblationSuiteRunRequest):
    """Run a baseline + single-disable ablation suite."""
    try:
        return run_ablation_suite(
            get_workspace(),
            request.eval_ids,
            request.components,
            repetitions=request.repetitions,
            mode=request.mode,  # type: ignore[arg-type]
            persist=request.persist,
        )
    except ValueError as exc:
        raise_400(str(exc))


@router.get("/ablation/suites")
async def ablation_suite_list(limit: int = 50):
    """List persisted ablation suites."""
    return list_ablation_suites(get_workspace(), limit=min(max(limit, 1), 200))


@router.post("/ablation/suites")
async def ablation_suite_create(request: AblationSuiteCreateRequest):
    """Create a persisted ablation suite without running it."""
    try:
        return create_ablation_suite(
            get_workspace(),
            request.eval_ids,
            request.components,
            repetitions=request.repetitions,
            mode=request.mode,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise_400(str(exc))


@router.get("/ablation/suites/{suite_id}")
async def ablation_suite_get(suite_id: str):
    """Return persisted suite definition, matrix, results, report, and artifact paths."""
    try:
        return get_ablation_suite(get_workspace(), suite_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.post("/ablation/suites/{suite_id}/run")
async def ablation_suite_run_existing(suite_id: str):
    """Run an existing persisted ablation suite."""
    try:
        return run_persisted_ablation_suite(get_workspace(), suite_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/ablation/suites/{suite_id}/report")
async def ablation_suite_report(suite_id: str):
    """Return or rebuild the component necessity report for a suite."""
    try:
        return get_ablation_report(get_workspace(), suite_id)
    except ValueError as exc:
        raise_404(str(exc))


@router.get("/ablation/suites/{suite_id}/artifacts")
async def ablation_suite_artifacts(suite_id: str):
    """Return artifact paths for a persisted ablation suite."""
    try:
        return get_ablation_artifacts(get_workspace(), suite_id)
    except ValueError as exc:
        raise_404(str(exc))


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
