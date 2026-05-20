"""D3 Observability service tests."""

from src.api.services.event_store import EventStore
from src.api.services.observability_service import build_run_observability, build_workspace_observability


def test_build_run_observability_empty_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("obs-run", "test", str(workspace))

    result = build_run_observability("obs-run", str(workspace))
    assert result["thread_id"] == "obs-run"
    assert result["status"] == "running" or result["status"] == "not_found"
    assert "tool_metrics" in result
    assert isinstance(result["tool_metrics"]["by_tool"], dict)


def test_build_run_observability_with_stages(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    session = store.create_session("obs-run-2", "test", str(workspace))
    import time
    t0 = time.time()
    store.update_session("obs-run-2", str(workspace), execution_plan={
        "stages": [
            {"id": "plan", "title": "规划", "status": "completed", "started_at": t0, "completed_at": t0 + 4},
            {"id": "implement", "title": "实现", "status": "running", "started_at": t0 + 4},
        ]
    })
    store.append_event("obs-run-2", "tool_call_finished", title="read file",
                       payload={"tool": "read_file", "stage_id": "plan", "ok": True},
                       workspace_dir=str(workspace))
    store.append_event("obs-run-2", "tool_call_finished", title="edit file",
                       payload={"tool": "edit_file", "stage_id": "implement", "ok": False},
                       workspace_dir=str(workspace))
    store.append_event("obs-run-2", "file_changed", title="changed",
                       payload={"path": "app.py", "stage_id": "implement"},
                       workspace_dir=str(workspace))
    store.append_event("obs-run-2", "error", title="oops",
                       payload={"stage_id": "implement"},
                       workspace_dir=str(workspace))

    result = build_run_observability("obs-run-2", str(workspace))
    assert result["thread_id"] == "obs-run-2"

    # Stage metrics
    assert len(result["stage_metrics"]) == 2
    plan_stage = next(s for s in result["stage_metrics"] if s["stage_id"] == "plan")
    assert plan_stage["duration_ms"] == 4000
    assert plan_stage["tool_calls"] == 1

    # Tool metrics
    assert result["tool_metrics"]["total"] == 2
    assert result["tool_metrics"]["failed"] >= 1
    assert "read_file" in result["tool_metrics"]["by_tool"]

    # File changes
    assert len(result["file_changes"]) == 1
    assert result["file_changes"][0]["path"] == "app.py"


def test_build_run_observability_policy_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("obs-run-3", "test", str(workspace))
    store.append_event("obs-run-3", "tool_policy_blocked", title="blocked",
                       payload={"tool": "bash"}, workspace_dir=str(workspace))
    store.append_event("obs-run-3", "tool_policy_checked", title="checked",
                       payload={"tool": "read_file"}, workspace_dir=str(workspace))

    result = build_run_observability("obs-run-3", str(workspace))
    assert result["policy"]["violations"] == 1
    assert len(result["policy"]["checks"]) == 2


def test_build_run_observability_not_found():
    result = build_run_observability("nonexistent", workspace_dir=".")
    assert result["status"] == "not_found"


def test_build_workspace_observability(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runs_dir = workspace / ".nanocursor" / "runs" / "r1"
    runs_dir.mkdir(parents=True)
    (runs_dir / "session.json").write_text(
        '{"thread_id":"r1","status":"completed","prompt":"test"}', encoding="utf-8"
    )
    (runs_dir / "events.jsonl").write_text(
        '{"type":"tool_call_finished","payload":{"tool":"read_file"}}\n'
        '{"type":"error"}\n',
        encoding="utf-8",
    )

    result = build_workspace_observability(str(workspace))
    assert len(result["runs"]) == 1
    assert result["trend"]["total_runs"] == 1
    assert result["trend"]["avg_tool_calls"] == 1.0
    assert result["trend"]["avg_errors"] == 1.0
