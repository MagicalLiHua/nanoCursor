from fastapi.testclient import TestClient

from src.api.services.agent_loop_eval_service import (
    get_agent_loop_eval_run,
    list_agent_loop_eval_cases,
    run_agent_loop_eval_suite,
)


def test_agent_loop_eval_suite_passes_core_cases(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_agent_loop_eval_suite(workspace_dir=str(workspace))

    assert result["suite"] == "agent_loop_core"
    assert result["total"] >= 8
    assert result["failed"] == 0
    assert result["pass_rate"] == 1.0
    assert result["failed_case_ids"] == []
    assert result["eval_run_id"].startswith("agent-loop-core-")
    persisted = get_agent_loop_eval_run(result["eval_run_id"], str(workspace))
    assert persisted["total"] == result["total"]


def test_agent_loop_eval_reports_missing_case(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_agent_loop_eval_suite(["missing-case"], workspace_dir=str(workspace), persist=False)

    assert result["total"] == 1
    assert result["failed"] == 1
    assert result["failed_case_ids"] == ["missing-case"]
    assert result["results"][0]["overall"] == "error"


def test_agent_loop_eval_catalog_contains_recovery_and_parallel_cases():
    ids = {case["id"] for case in list_agent_loop_eval_cases()}

    assert "feature_failed_task_creates_recovery" in ids
    assert "parallel_analysis_spawns_read_only_agent" in ids
    assert "risky_operation_requests_approval" in ids


def test_agent_loop_eval_api_routes():
    from src.api.server import app

    client = TestClient(app)
    catalog = client.get("/api/evals/agent-loop/catalog")
    run = client.post("/api/evals/agent-loop/run", json={"persist": False})

    assert catalog.status_code == 200
    assert catalog.json()["suite"] == "agent_loop_core"
    assert run.status_code == 200
    assert run.json()["failed"] == 0
