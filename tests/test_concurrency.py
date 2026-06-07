"""Tests for concurrency control."""

from fastapi.testclient import TestClient

from src.api import legacy_runtime as api_server


def test_concurrent_run_limit_enforced(tmp_path, monkeypatch):
    """When MAX_CONCURRENT_RUNS is reached, new runs should get 429."""
    client = TestClient(api_server.app)

    # Set max concurrent runs to 2
    monkeypatch.setattr(api_server.config_module, "MAX_CONCURRENT_RUNS", 2)

    async def fake_stream(**kwargs):
        yield ("token", "ok")
        yield ("metrics", 10, 5)
        yield ("done", "ok")

    async def fake_briefing(**kwargs):
        return {"enabled": False, "results": [], "contributions": {"contributions": []}, "briefing": ""}

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_stream)
    monkeypatch.setattr(api_server, "run_parallel_agent_briefing", fake_briefing)

    # Use separate workspaces to avoid workspace lock conflict
    ws1 = tmp_path / "ws1"
    ws1.mkdir()
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    ws3 = tmp_path / "ws3"
    ws3.mkdir()

    # Start first run
    r1 = client.post("/api/run", json={"prompt": "task 1", "workspace_dir": str(ws1)})
    assert r1.status_code == 200

    # Start second run
    r2 = client.post("/api/run", json={"prompt": "task 2", "workspace_dir": str(ws2)})
    assert r2.status_code == 200

    # Third run should be rejected due to concurrency limit
    r3 = client.post("/api/run", json={"prompt": "task 3", "workspace_dir": str(ws3)})
    assert r3.status_code == 429

    # Wait for runs to complete
    import time
    time.sleep(1)

    # Clean up active runs
    with api_server.runs_lock:
        for tid in list(api_server.active_runs.keys()):
            api_server.active_runs[tid].set_status("completed")
            api_server.active_runs.pop(tid, None)

    # After runs complete, new runs should be allowed
    ws4 = tmp_path / "ws4"
    ws4.mkdir()
    r4 = client.post("/api/run", json={"prompt": "task 4", "workspace_dir": str(ws4)})
    assert r4.status_code == 200
