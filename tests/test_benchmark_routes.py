from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.api.services.benchmark_service import (
    get_real_task_benchmark_run,
    list_real_task_benchmarks,
    run_real_task_benchmark_suite,
)


def test_real_task_benchmark_suite_scores_core_cases(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    catalog = list_real_task_benchmarks(str(workspace))
    assert {case["difficulty"] for case in catalog} >= {"easy", "medium", "hard"}

    result = run_real_task_benchmark_suite(workspace_dir=str(workspace))

    assert result["suite"] == "real_tasks"
    assert result["total"] >= 6
    assert result["failed"] == 0
    assert result["routing_accuracy"] == 1.0
    assert result["tool_policy_accuracy"] == 1.0
    assert result["test_pass_rate"] == 1.0
    assert result["benchmark_run_id"].startswith("real-tasks-")
    persisted = get_real_task_benchmark_run(result["benchmark_run_id"], str(workspace))
    assert persisted["total"] == result["total"]


def test_real_task_benchmark_reports_missing_case(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_real_task_benchmark_suite(["missing-case"], workspace_dir=str(workspace), persist=False)

    assert result["total"] == 1
    assert result["failed"] == 1
    assert result["results"][0]["overall"] == "error"


def test_benchmark_routes_start_and_finalize_run(tmp_path, monkeypatch):
    from src.api import legacy_runtime as api_server
    import src.api.services.benchmark_service as benchmark_service
    from src.api.services.benchmark_service import emit_benchmark_run as original_emit

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_workspace = api_server._get_workspace()

    def emit_without_delay(*, thread_id, benchmark_id, workspace_dir, store, status_callback=None):
        return original_emit(
            thread_id=thread_id,
            benchmark_id=benchmark_id,
            workspace_dir=workspace_dir,
            store=store,
            delay=0,
            status_callback=status_callback,
        )

    monkeypatch.setattr(benchmark_service, "emit_benchmark_run", emit_without_delay)

    try:
        api_server._set_active_workspace(str(workspace))
        client = TestClient(api_server.app)

        response = client.get("/api/benchmarks")
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["benchmarks"]} >= {"python-utils"}

        response = client.post(
            "/api/benchmarks/run",
            json={"benchmark_id": "python-utils", "workspace_dir": str(workspace)},
        )
        assert response.status_code == 200
        thread_id = response.json()["thread_id"]

        deadline = time.time() + 2
        session = None
        while time.time() < deadline:
            session = api_server.event_store.get_session(thread_id, str(workspace))
            if session and session.get("status") == "completed":
                break
            time.sleep(0.02)

        assert session is not None
        assert session["status"] == "completed"
        events = api_server.event_store.list_events(thread_id, str(workspace))
        assert any(event.type == "benchmark_finished" for event in events)
        assert (workspace / "benchmarks" / "python-utils" / "string_tools.py").exists()
        assert api_server.run_manager.get(thread_id) is None
    finally:
        api_server._set_active_workspace(old_workspace)


def test_real_task_benchmark_routes(tmp_path, monkeypatch):
    from src.api.server import app
    import src.infra.config as config_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", str(workspace))
    client = TestClient(app)

    catalog = client.get("/api/benchmarks/real-tasks")
    assert catalog.status_code == 200
    assert catalog.json()["suite"] == "real_tasks"
    assert {case["difficulty"] for case in catalog.json()["benchmarks"]} >= {"easy", "medium", "hard"}

    run = client.post("/api/benchmarks/real-tasks/run", json={"case_ids": ["easy-greeting"], "persist": True})
    assert run.status_code == 200
    data = run.json()
    assert data["total"] == 1
    assert data["passed"] == 1
    assert data["routing_accuracy"] == 1.0

    persisted = client.get(f"/api/benchmarks/real-tasks/runs/{data['benchmark_run_id']}")
    assert persisted.status_code == 200
    assert persisted.json()["benchmark_run_id"] == data["benchmark_run_id"]
