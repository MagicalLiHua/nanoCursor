from fastapi.testclient import TestClient

from src.api.services.intent_eval_service import (
    get_intent_eval_run,
    list_intent_eval_cases,
    run_intent_eval_suite,
)


def test_intent_eval_catalog_contains_core_cases():
    cases = list_intent_eval_cases()
    ids = {case["id"] for case in cases}

    assert len(cases) >= 120
    assert "greeting_direct_answer" in ids
    assert "python_script_feature_delivery" in ids
    assert "delete_files_risky_operation" in ids


def test_intent_eval_suite_passes_core_cases(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_intent_eval_suite(workspace_dir=str(workspace))

    assert result["suite"] == "intent_core"
    assert result["total"] >= 120
    assert result["failed"] == 0
    assert result["pass_rate"] == 1.0
    assert result["failed_case_ids"] == []
    assert result["failures"] == []
    assert result["metrics"]["high_risk_recall"] == 1.0
    assert result["metrics"]["no_write_compliance"] == 1.0
    assert result["metrics"]["direct_answer_precision_proxy"] == 1.0
    assert result["metrics"]["semantic_used_count"] == 0
    assert result["metrics"]["deterministic_hint_counts"]["code_artifact_hint"] > 0
    assert result["metrics"]["deterministic_hint_counts"]["workspace_read_hint"] > 0
    assert result["eval_run_id"].startswith("intent-core-")
    persisted = get_intent_eval_run(result["eval_run_id"], str(workspace))
    assert persisted["total"] == result["total"]


def test_intent_eval_suite_reports_missing_case(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_intent_eval_suite(["missing-case"], workspace_dir=str(workspace), persist=False)

    assert result["total"] == 1
    assert result["failed"] == 1
    assert result["failed_case_ids"] == ["missing-case"]
    assert result["failures"][0]["id"] == "missing-case"
    assert result["results"][0]["overall"] == "error"


def test_intent_eval_api_routes():
    from src.api.server import app

    client = TestClient(app)
    catalog = client.get("/api/evals/intent/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["suite"] == "intent_core"

    cases = client.get("/api/evals/intent/cases")
    assert cases.status_code == 200
    assert cases.json()["suite"] == "intent_core"
    assert cases.json()["cases"]

    run = client.post("/api/evals/intent/run", json={"case_ids": ["greeting_direct_answer"], "persist": False})
    assert run.status_code == 200
    data = run.json()
    assert data["total"] == 1
    assert data["passed"] == 1
