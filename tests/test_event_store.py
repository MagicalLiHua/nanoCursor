import json

from src.api.services.event_store import EventStore
from src.api.services.run_context import RunContext
from src.api.services.run_history import list_run_history, list_run_history_with_active, rebuild_run_index
from src.runtime.run_manager import RunManager


def test_event_store_creates_session_and_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()

    session = store.create_session(
        thread_id="run-1",
        prompt="build a todo app",
        workspace_dir=str(workspace),
    )
    event = store.append_event(
        thread_id="run-1",
        event_type="run_started",
        title="started",
        content="build a todo app",
        payload={"workspace_dir": str(workspace)},
        workspace_dir=str(workspace),
    )

    assert session["thread_id"] == "run-1"
    assert event.type == "run_started"
    assert event.payload["workspace_dir"] == str(workspace)

    run_dir = workspace / ".nanocursor" / "runs" / "run-1"
    assert (run_dir / "session.json").exists()
    assert (run_dir / "events.jsonl").exists()


def test_event_store_lists_events_after_cursor(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-1", "prompt", str(workspace))

    store.append_event("run-1", "run_started", workspace_dir=str(workspace))
    store.append_event("run-1", "tool_call_finished", workspace_dir=str(workspace))
    store.append_event("run-1", "done", workspace_dir=str(workspace))

    events = store.list_events("run-1", str(workspace), after=1)

    assert [event.type for event in events] == ["tool_call_finished", "done"]
    assert store.count_events("run-1", str(workspace)) == 3


def test_event_store_updates_session_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-1", "prompt", str(workspace))

    updated = store.update_session("run-1", str(workspace), status="completed")

    assert updated["status"] == "completed"
    saved = json.loads(
        (workspace / ".nanocursor" / "runs" / "run-1" / "session.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["status"] == "completed"


def test_list_run_history_sorts_and_summarizes_artifacts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()

    store.create_session("older", "old prompt", str(workspace), status="failed")
    store.create_session("newer", "new prompt", str(workspace), status="completed", mode="agenthub_demo")

    older_session = store.session_path("older", str(workspace))
    newer_session = store.session_path("newer", str(workspace))
    older_data = json.loads(older_session.read_text(encoding="utf-8"))
    newer_data = json.loads(newer_session.read_text(encoding="utf-8"))
    older_data.update({"created_at": 1, "updated_at": 1})
    newer_data.update({"created_at": 2, "updated_at": 3})
    older_session.write_text(json.dumps(older_data), encoding="utf-8")
    newer_session.write_text(json.dumps(newer_data), encoding="utf-8")

    store.append_event("newer", "run_started", workspace_dir=str(workspace))
    store.append_event("newer", "done", workspace_dir=str(workspace))
    run_dir = store.run_dir("newer", str(workspace))
    (run_dir / "diff.patch").write_text("diff", encoding="utf-8")
    (run_dir / "report.md").write_text("# report", encoding="utf-8")
    (run_dir / "changed_files.json").write_text(
        json.dumps([{"path": "app.py"}]), encoding="utf-8"
    )

    runs = list_run_history(str(workspace))

    assert [run["thread_id"] for run in runs] == ["newer", "older"]
    assert runs[0]["event_count"] == 2
    assert runs[0]["last_event_type"] == "done"
    assert runs[0]["changed_files_count"] == 1
    assert runs[0]["has_diff"] is True
    assert runs[0]["has_report"] is True
    index = json.loads((workspace / ".nanocursor" / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == 1
    assert [run["thread_id"] for run in index["runs"]] == ["newer", "older"]


def test_list_run_history_summarizes_event_diff_and_delivery_report(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()

    store.create_session("run-1", "prompt", str(workspace), status="completed")
    store.append_event(
        "run-1",
        "diff_updated",
        workspace_dir=str(workspace),
        payload={
            "diff": "diff --git a/app.py b/app.py",
            "changed_files": [
                {"path": "app.py", "change_type": "created"},
                {"path": "test_app.py", "change_type": "created"},
            ],
        },
    )
    run_dir = store.run_dir("run-1", str(workspace))
    (run_dir / "delivery.md").write_text("# delivery", encoding="utf-8")

    runs = list_run_history(str(workspace))

    assert runs[0]["changed_files_count"] == 2
    assert runs[0]["has_diff"] is True
    assert runs[0]["has_report"] is True


def test_list_run_history_ignores_lead_direct_placeholder_report(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()

    store.create_session("run-1", "哈喽", str(workspace), status="completed")
    store.update_session("run-1", str(workspace), execution_plan={"strategy": "lead_direct_reply"})
    run_dir = store.run_dir("run-1", str(workspace))
    (run_dir / "delivery.md").write_text("# placeholder", encoding="utf-8")
    (run_dir / "delivery.json").write_text("{}", encoding="utf-8")

    runs = list_run_history(str(workspace))

    assert runs[0]["changed_files_count"] == 0
    assert runs[0]["has_report"] is False


def test_list_run_history_filters_and_limits(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("a", "prompt", str(workspace), status="completed", mode="agenthub_demo")
    store.create_session("b", "prompt", str(workspace), status="failed", mode="agenthub_delivery")

    runs = list_run_history(str(workspace), status="completed", mode="agenthub_demo", limit=1)

    assert len(runs) == 1
    assert runs[0]["thread_id"] == "a"


def test_run_index_updates_on_session_status_change(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()

    store.create_session("run-1", "prompt", str(workspace), status="running")
    store.update_session("run-1", str(workspace), status="completed")

    index = json.loads((workspace / ".nanocursor" / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["runs"][0]["thread_id"] == "run-1"
    assert index["runs"][0]["status"] == "completed"


def test_list_run_history_rebuilds_corrupted_index(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-1", "prompt", str(workspace), status="completed")

    index_path = workspace / ".nanocursor" / "runs" / "index.json"
    index_path.write_text("{bad json", encoding="utf-8")

    runs = list_run_history(str(workspace))
    rebuilt = rebuild_run_index(str(workspace))

    assert runs[0]["thread_id"] == "run-1"
    assert rebuilt["runs"][0]["thread_id"] == "run-1"


def test_list_run_history_with_active_overlays_runtime_state(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-1", "prompt", str(workspace), status="created")

    manager = RunManager()
    manager.register(
        RunContext(
            thread_id="run-1",
            workspace_dir=str(workspace),
            queue=None,  # type: ignore[arg-type]
            metadata={"prompt": "prompt", "mode": "agenthub_delivery"},
        )
    )

    runs = list_run_history_with_active(manager, str(workspace))

    assert runs[0]["thread_id"] == "run-1"
    assert runs[0]["status"] == "running"
    assert runs[0]["is_active"] is True
    assert runs[0]["is_write_mode"] is True
    assert runs[0]["source"] == "history+active"
