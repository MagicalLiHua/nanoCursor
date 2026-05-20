"""D2 Real Eval Runner tests."""

import json
from pathlib import Path

from src.api.services.eval_service import (
    compare_eval_runs, load_eval_tasks, prepare_eval_workspace, run_eval, score_eval_run,
)
from src.api.services.event_store import EventStore


def test_load_eval_tasks_merges_json_and_starter():
    tasks = load_eval_tasks()
    assert len(tasks) >= 2
    ids = [t["id"] for t in tasks]
    assert "todo_web_app" in ids
    assert "bug_fix_import_error" in ids


def test_load_eval_tasks_task_has_fixture():
    bug_fix = next((t for t in load_eval_tasks() if t["id"] == "bug_fix_import_error"), None)
    assert bug_fix is not None
    assert "fixture" in bug_fix
    assert bug_fix.get("test_command") == "pytest -q"


def test_prepare_eval_workspace_creates_copy(tmp_path, monkeypatch):
    """Verify fixture copy doesn't pollute original."""
    import src.api.services.eval_service as svc
    monkeypatch.setattr(svc, "_workspace", lambda: tmp_path / "workspace")
    monkeypatch.setattr(svc, "EVAL_FIXTURES_DIR", svc.PROJECT_ROOT / "evals" / "fixtures")

    ws = prepare_eval_workspace("bug_fix_import_error")
    assert ws.exists()
    assert (ws / "app" / "main.py").exists()
    assert (ws / "tests" / "test_import.py").exists()
    assert ".nanocursor" in str(ws)


def test_run_eval_json_task_uses_fixture_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()

    result = run_eval("bug_fix_import_error", str(workspace), store)

    eval_workspace = result["workspace_dir"]
    assert ".nanocursor" in eval_workspace
    assert result["score"]["overall"] == "passed"
    assert (workspace / ".nanocursor" / "evals" / result["eval_run_id"] / "result.json").exists()
    assert (workspace / ".nanocursor" / "eval_workspaces").exists()
    assert (workspace / ".nanocursor" / "eval_workspaces").is_dir()
    assert (Path(eval_workspace) / "app" / "util.py").exists()
    saved = json.loads((workspace / ".nanocursor" / "evals" / result["eval_run_id"] / "result.json").read_text(encoding="utf-8"))
    assert saved["workspace_dir"] == eval_workspace


def test_score_eval_run_required_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("eval-run", "test", str(workspace))
    store.append_event("eval-run", "plan_created", title="plan", agent="lead", workspace_dir=str(workspace))
    store.append_event("eval-run", "tool_call_finished", title="tool", agent="coder", workspace_dir=str(workspace))
    store.append_event("eval-run", "done", title="done", agent="lead", workspace_dir=str(workspace))
    store.update_session("eval-run", str(workspace), status="completed")

    score = score_eval_run("eval-run", str(workspace), {
        "required_events": ["plan_created", "tool_call_finished", "done"],
    })
    assert score["overall"] == "passed"


def test_score_eval_run_detects_forbidden_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("eval-run", "test", str(workspace))
    store.append_event("eval-run", "file_changed", title="changed", agent="coder",
                       payload={"path": ".env"}, workspace_dir=str(workspace))

    score = score_eval_run("eval-run", str(workspace), {
        "forbidden_paths": [".env"],
    })
    assert score["overall"] == "failed"


def test_score_eval_run_detects_missing_required_files(tmp_path):
    score = score_eval_run("eval-run", str(tmp_path / "workspace"), {
        "required_files": ["nonexistent.py"],
    })
    assert score["overall"] == "failed"


def test_compare_eval_runs_empty():
    result = compare_eval_runs("nonexistent_eval", limit=5)
    assert result["total_runs"] == 0
    assert result["pass_rate"] == 0.0
