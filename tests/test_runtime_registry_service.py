import json
import queue
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.services.event_store import EventStore
from src.api.services.run_context import RunContext
from src.api.services.runtime_lifecycle_service import (
    recover_interrupted_runs,
    save_active_runs_state,
)
from src.api.services.runtime_registry_service import RuntimeRegistry, get_runtime_registry
from src.runtime.run_manager import RunManager


def test_api_state_modules_share_the_process_runtime_registry():
    from src.api import legacy_runtime as api_server
    from src.api import run_state
    from src.api import runtime_facade

    registry = get_runtime_registry()

    assert api_server.run_manager is registry.run_manager
    assert run_state.run_manager is registry.run_manager
    assert runtime_facade.get_run_manager() is registry.run_manager
    assert api_server.event_store is registry.event_store
    assert run_state.event_store is registry.event_store
    assert runtime_facade.get_event_store() is registry.event_store


def test_official_app_reads_runs_from_shared_registry(tmp_path):
    from src.api.server import app

    registry = get_runtime_registry()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = RunContext(thread_id="official-run", workspace_dir=str(workspace), queue=queue.Queue())
    registry.run_manager.register(context)

    try:
        response = TestClient(app).get("/api/runs/active")
        assert response.status_code == 200
        assert "official-run" in [item["thread_id"] for item in response.json()["active_runs"]]
    finally:
        registry.run_manager.unregister("official-run")


def test_save_active_runs_state_uses_registry_snapshot(tmp_path):
    registry = RuntimeRegistry(RunManager(), EventStore())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = RunContext(
        thread_id="run-1",
        workspace_dir=str(workspace),
        queue=queue.Queue(),
        conversation_id="conv-1",
    )
    registry.run_manager.register(context)
    state_path = tmp_path / "active-runs.json"

    snapshot = save_active_runs_state(registry, path=state_path)

    assert snapshot["run-1"]["conversation_id"] == "conv-1"
    assert json.loads(state_path.read_text(encoding="utf-8")) == snapshot


def test_save_active_runs_state_logs_best_effort_failure(tmp_path, monkeypatch):
    from src.api.services import runtime_lifecycle_service

    registry = RuntimeRegistry(RunManager(), EventStore())
    invalid_path = tmp_path / "directory-target"
    invalid_path.mkdir()
    warnings = []
    monkeypatch.setattr(
        runtime_lifecycle_service.logger,
        "warning",
        lambda event, *args, **kwargs: warnings.append((event, kwargs)),
    )

    snapshot = save_active_runs_state(registry, path=invalid_path)

    assert snapshot == {}
    assert warnings[0][0] == "active_runs_state_persist_failed"
    assert warnings[0][1]["extra"]["path"] == str(invalid_path)


def test_recover_interrupted_runs_uses_registry_event_store(tmp_path):
    registry = RuntimeRegistry(RunManager(), EventStore())
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".nanocursor" / "runs" / "abandoned"
    run_dir.mkdir(parents=True)
    (run_dir / "session.json").write_text(
        json.dumps(
            {
                "thread_id": "abandoned",
                "workspace_dir": str(workspace),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_interrupted_runs(registry, workspace_dir=str(workspace))

    assert recovered == ["abandoned"]
    session = json.loads((run_dir / "session.json").read_text(encoding="utf-8"))
    assert session["status"] == "interrupted"
    events = registry.event_store.list_events("abandoned", str(workspace))
    assert events[-1].type == "error"
    assert events[-1].payload["reason"] == "server_shutdown"


def test_official_state_demo_and_benchmark_routes_do_not_load_legacy_runtime():
    code = """
import os
import sys
import time
from fastapi.testclient import TestClient
from src.api.server import app
import src.api.services.benchmark_service as benchmark_service
from src.api.services.runtime_registry_service import get_run_manager

os.environ["NANOCURSOR_DEMO_EVENT_DELAY"] = "0"
original_emit_benchmark_run = benchmark_service.emit_benchmark_run
def emit_benchmark_without_delay(*args, **kwargs):
    kwargs["delay"] = 0
    return original_emit_benchmark_run(*args, **kwargs)
benchmark_service.emit_benchmark_run = emit_benchmark_without_delay
client = TestClient(app)
assert client.get("/api/runs/active").status_code == 200
assert client.get("/api/benchmarks").status_code == 200
response = client.post("/api/runs/demo", json={"prompt": "boundary demo"})
assert response.status_code == 200
for thread_id in (response.json()["thread_id"],):
    for _ in range(200):
        if get_run_manager().get(thread_id) is None:
            break
        time.sleep(0.01)
    assert get_run_manager().get(thread_id) is None
benchmark = client.post("/api/benchmarks/run", json={"benchmark_id": "python-utils"})
assert benchmark.status_code == 200
for thread_id in (benchmark.json()["thread_id"],):
    for _ in range(200):
        if get_run_manager().get(thread_id) is None:
            break
        time.sleep(0.01)
    assert get_run_manager().get(thread_id) is None
assert "src.api.legacy_runtime" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
