import json
import subprocess
import sys

from fastapi.testclient import TestClient

from src.api.services.agent_eval_service import (
    get_agent_eval_run,
    list_agent_eval_runs,
    run_agent_eval_suite,
    summarize_agent_eval_runs,
)


def test_agent_eval_core_suite_passes_and_persists(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_agent_eval_suite(workspace_dir=str(workspace))

    assert result["suite"] == "agent_core"
    assert result["status"] == "passed"
    assert result["failed"] == 0
    assert result["total"] > 0
    assert result["eval_run_id"].startswith("agent-core-")
    assert {section["id"] for section in result["sections"]} == {
        "intent_routing",
        "tool_policy",
        "task_scoring",
        "runtime_context",
    }
    runtime_section = next(section for section in result["sections"] if section["id"] == "runtime_context")
    assert runtime_section["status"] == "passed"
    assert {case["id"] for case in runtime_section["cases"]} == {
        "context_selection_accuracy",
        "workspace_scope_isolation",
        "recovery_context_injection",
    }

    persisted = get_agent_eval_run(result["eval_run_id"], str(workspace))
    assert persisted["total"] == result["total"]
    assert persisted["status"] == "passed"


def test_agent_eval_can_restrict_task_eval_ids_without_persisting(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_agent_eval_suite(
        workspace_dir=str(workspace),
        persist=False,
        task_eval_ids=["bug_fix_import_error"],
    )

    assert "eval_run_id" not in result
    task_section = next(section for section in result["sections"] if section["id"] == "task_scoring")
    assert task_section["total"] == 1
    assert task_section["results"][0]["id"] == "bug_fix_import_error"
    assert task_section["status"] == "passed"
    assert next(section for section in result["sections"] if section["id"] == "runtime_context")["total"] == 3


def test_agent_eval_history_and_summary(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = run_agent_eval_suite(workspace_dir=str(workspace), task_eval_ids=["bug_fix_import_error"])
    second = run_agent_eval_suite(workspace_dir=str(workspace), task_eval_ids=["missing-eval"])

    history = list_agent_eval_runs(str(workspace), limit=10)
    assert history["total_runs"] == 2
    assert [run["eval_run_id"] for run in history["runs"]] == [second["eval_run_id"], first["eval_run_id"]]
    assert history["runs"][0]["status"] == "failed"
    assert history["runs"][0]["failed_sections"] == ["task_scoring"]

    summary = summarize_agent_eval_runs(str(workspace), limit=10)
    assert summary["total_runs"] == 2
    assert summary["passed_runs"] == 1
    assert summary["failed_runs"] == 1
    assert summary["run_pass_rate"] == 0.5
    assert summary["latest_run"]["eval_run_id"] == second["eval_run_id"]
    assert any(section["id"] == "task_scoring" and section["failed"] == 1 for section in summary["section_trends"])


def test_run_agent_evals_cli_json(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_agent_evals.py",
            "--workspace-dir",
            str(workspace),
            "--task-eval",
            "bug_fix_import_error",
            "--no-persist",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["suite"] == "agent_core"
    assert result["status"] == "passed"
    assert "eval_run_id" not in result


def test_run_agent_evals_cli_summary_json(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_agent_eval_suite(workspace_dir=str(workspace), task_eval_ids=["bug_fix_import_error"])

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_agent_evals.py",
            "--workspace-dir",
            str(workspace),
            "--summary",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["suite"] == "agent_core"
    assert result["total_runs"] == 1
    assert result["passed_runs"] == 1


def test_agent_eval_api_routes():
    from api_server import app

    client = TestClient(app)
    catalog = client.get("/api/evals/agent/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["suite"] == "agent_core"

    run = client.post(
        "/api/evals/agent/run",
        json={"suite": "core", "task_eval_ids": ["bug_fix_import_error"], "persist": False},
    )
    assert run.status_code == 200
    data = run.json()
    assert data["suite"] == "agent_core"
    assert data["status"] == "passed"
    assert data["failed"] == 0
    assert "eval_run_id" not in data


def test_agent_eval_api_returns_persisted_result():
    from api_server import app

    client = TestClient(app)
    run = client.post(
        "/api/evals/agent/run",
        json={"suite": "core", "task_eval_ids": ["bug_fix_import_error"], "persist": True},
    )
    assert run.status_code == 200
    eval_run_id = run.json()["eval_run_id"]

    persisted = client.get(f"/api/evals/agent/runs/{eval_run_id}")
    assert persisted.status_code == 200
    assert persisted.json()["eval_run_id"] == eval_run_id

    history = client.get("/api/evals/agent/runs")
    assert history.status_code == 200
    assert history.json()["total_runs"] >= 1

    summary = client.get("/api/evals/agent/summary")
    assert summary.status_code == 200
    assert summary.json()["total_runs"] >= 1
