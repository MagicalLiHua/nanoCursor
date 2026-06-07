from fastapi.testclient import TestClient

from src.api.services.routing_eval_service import (
    get_routing_eval_run,
    list_routing_eval_cases,
    run_routing_eval_suite,
)


def test_routing_eval_catalog_contains_core_cases():
    cases = list_routing_eval_cases()
    ids = {case["id"] for case in cases}

    assert len(cases) >= 7
    assert "greeting_lead_only" in ids
    assert "single_file_small_code_edit" in ids
    assert "code_edit_with_tests" in ids
    assert "github_issue_selects_mcp" in ids
    assert "python_refactor_selects_skill" in ids


def test_routing_eval_suite_passes_core_cases(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_routing_eval_suite(workspace_dir=str(workspace))

    assert result["suite"] == "routing_core"
    assert result["total"] >= 7
    assert result["failed"] == 0
    assert result["pass_rate"] == 1.0
    assert result["eval_run_id"].startswith("routing-core-")
    persisted = get_routing_eval_run(result["eval_run_id"], str(workspace))
    assert persisted["total"] == result["total"]


def test_routing_eval_suite_reports_missing_case(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_routing_eval_suite(["missing-case"], workspace_dir=str(workspace), persist=False)

    assert result["total"] == 1
    assert result["failed"] == 1
    assert result["results"][0]["overall"] == "error"


def test_routing_eval_api_routes(tmp_path, monkeypatch):
    from src.api.server import app
    import src.infra.config as config_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(workspace))
    client = TestClient(app)

    catalog = client.get("/api/evals/routing/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["suite"] == "routing_core"

    run = client.post("/api/evals/routing/run", json={"case_ids": ["greeting_lead_only"], "persist": False})
    assert run.status_code == 200
    data = run.json()
    assert data["total"] == 1
    assert data["passed"] == 1
