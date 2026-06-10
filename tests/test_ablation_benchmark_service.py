from src.api.services.ablation_benchmark_service import (
    build_ablation_matrix,
    build_component_necessity_report,
    list_ablation_components,
    run_ablation_suite,
    save_ablation_artifacts,
)
from src.api.services.ablation_config_service import is_component_enabled, make_ablation_config


def test_ablation_config_does_not_disable_normal_runs():
    assert is_component_enabled("context_pack") is True

    config = make_ablation_config(
        eval_id="small_python_bugfix",
        variant_id="disable_context_pack",
        disabled_components=["context-pack"],
    )

    assert is_component_enabled("context_pack", config) is False
    assert is_component_enabled("project_index", config) is True
    assert config.disabled_components == ["context_pack"]


def test_build_ablation_matrix_baseline_and_single_disable():
    matrix = build_ablation_matrix(
        ["small_python_bugfix", "pytest_failure_repair"],
        ["context_pack", "failure_recovery", "unknown_component"],
        repetitions=2,
    )

    assert matrix["summary"]["eval_count"] == 2
    assert matrix["summary"]["component_count"] == 2
    assert matrix["summary"]["variant_count"] == 3
    assert matrix["summary"]["run_count"] == 12
    assert [variant["variant_id"] for variant in matrix["suite"]["variants"]] == [
        "baseline",
        "disable_context_pack",
        "disable_failure_recovery",
    ]
    assert matrix["matrix"][0]["config"]["variant_id"] == "baseline"


def test_component_lift_and_verdicts_are_explainable():
    matrix = build_ablation_matrix(
        ["small_python_bugfix"],
        ["context_pack", "failure_recovery", "skills"],
    )
    report = build_component_necessity_report({
        **matrix,
        "results": [
            {"variant_id": "baseline", "score": {"overall": "passed"}},
            {"variant_id": "disable_context_pack", "score": 0.72},
            {"variant_id": "disable_failure_recovery", "score": {"overall": "failed"}},
            {"variant_id": "disable_skills", "score": 0.98},
        ],
    })

    by_component = {item["component"]: item for item in report["components"]}

    assert by_component["failure_recovery"]["lift"] == 1.0
    assert by_component["failure_recovery"]["verdict"] == "necessary"
    assert by_component["context_pack"]["verdict"] == "necessary"
    assert by_component["skills"]["verdict"] == "neutral"
    assert report["summary"]["necessary"] == 2


def test_save_ablation_artifacts_writes_report(tmp_path):
    matrix = build_ablation_matrix(["small_python_bugfix"], ["context_pack"])
    saved = save_ablation_artifacts(str(tmp_path), {
        **matrix,
        "results": [
            {"variant_id": "baseline", "score": 1.0},
            {"variant_id": "disable_context_pack", "score": 0.7},
        ],
    })

    assert saved["suite_id"].startswith("ablation_")
    assert saved["report"]["components"][0]["verdict"] == "necessary"
    assert "report_md" in saved["artifacts"]


def test_run_ablation_suite_executes_matrix_and_persists(tmp_path):
    result = run_ablation_suite(
        str(tmp_path),
        ["todo_web_app"],
        ["context_pack"],
        mode="deterministic",
        persist=True,
    )

    assert result["summary"]["run_count"] == 2
    assert len(result["results"]) == 2
    assert result["summary"]["completed"] == 2
    assert result["report"]["components"][0]["component"] == "context_pack"
    assert result["artifacts"]["artifacts"]["report_md"].endswith("report.md")


def test_list_ablation_components_contains_planned_core_modules():
    ids = {item["id"] for item in list_ablation_components()}

    assert {"agent_loop", "context_pack", "project_index", "failure_recovery", "go_sidecars"} <= ids


def test_ablation_api_builds_matrix_and_report():
    from fastapi.testclient import TestClient
    from src.api.server import app

    client = TestClient(app)

    components = client.get("/api/evals/ablation/components")
    assert components.status_code == 200
    assert "failure_recovery" in {item["id"] for item in components.json()["components"]}

    matrix_response = client.post("/api/evals/ablation/matrix", json={
        "eval_ids": ["small_python_bugfix"],
        "components": ["failure_recovery"],
    })
    assert matrix_response.status_code == 200
    matrix = matrix_response.json()
    assert matrix["summary"]["run_count"] == 2

    report_response = client.post("/api/evals/ablation/report", json={
        "suite": matrix["suite"],
        "matrix": matrix["matrix"],
        "results": [
            {"variant_id": "baseline", "score": 1.0},
            {"variant_id": "disable_failure_recovery", "score": 0.0},
        ],
    })
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["components"][0]["component"] == "failure_recovery"
    assert report["components"][0]["verdict"] == "necessary"

    suite_response = client.post("/api/evals/ablation/suite/run", json={
        "eval_ids": ["todo_web_app"],
        "components": ["context_pack"],
        "persist": False,
    })
    assert suite_response.status_code == 200
    assert suite_response.json()["summary"]["run_count"] == 2
