from __future__ import annotations

import time

from fastapi.testclient import TestClient


def test_benchmark_routes_start_and_finalize_run(tmp_path, monkeypatch):
    import api_server
    import src.api.routes.benchmarks as benchmark_routes
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

    monkeypatch.setattr(benchmark_routes, "emit_benchmark_run", emit_without_delay)

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
