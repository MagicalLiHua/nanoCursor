from pathlib import Path

from scripts.run_real_task_smoke import TASKS, prepare_workspace, validate_outcome


def _task(task_id):
    return next(task for task in TASKS if task.task_id == task_id)


def test_validate_readme_analysis_outcome_passes():
    task = _task("readme_analysis")
    outcome = {
        "thread_id": "run-a",
        "status": "completed",
        "strategy": "analysis_only",
        "summary": {"final_message": "README 缺少 Quick Start。"},
        "stages": [{"id": "intake"}, {"id": "plan"}],
        "changes": {"files": [], "diff": ""},
        "team": {"members": [{"role": "lead"}], "runtime_source": ""},
    }

    result = validate_outcome(task, outcome, [])

    assert result["ok"] is True
    assert result["summary"]["changed_files_count"] == 0


def test_validate_readme_analysis_rejects_file_changes():
    task = _task("readme_analysis")
    outcome = {
        "thread_id": "run-a",
        "status": "completed",
        "strategy": "analysis_only",
        "summary": {"final_message": "Done"},
        "stages": [{"id": "intake"}, {"id": "plan"}],
        "changes": {"files": [{"path": "README.md", "change_type": "modified"}], "diff": "diff"},
        "team": {"members": [{"role": "lead"}], "runtime_source": ""},
    }

    result = validate_outcome(task, outcome, [])

    assert result["ok"] is False
    assert any("changed files expected <= 0" in error for error in result["errors"])


def test_validate_python_slugify_requires_diff_and_changes():
    task = _task("tiny_python_slugify")
    outcome = {
        "thread_id": "run-b",
        "status": "completed",
        "strategy": "feature_delivery",
        "summary": {"final_message": "Implemented slugify."},
        "stages": [{"id": "intake"}, {"id": "implement"}],
        "changes": {
            "files": [{"path": "src/tiny_pkg/slug.py", "change_type": "created"}],
            "diff": "diff --git a/src/tiny_pkg/slug.py b/src/tiny_pkg/slug.py",
        },
        "team": {"members": [{"role": "lead"}, {"role": "coder"}], "runtime_source": "runtime_recommended"},
    }

    assert validate_outcome(task, outcome, [])["ok"] is True


def test_validate_reports_pending_approval():
    task = _task("tiny_python_slugify")
    outcome = {
        "thread_id": "run-approval",
        "status": "waiting_approval",
        "strategy": "feature_delivery",
        "summary": {"final_message": ""},
        "stages": [{"id": "intake"}, {"id": "implement"}],
        "changes": {"files": [], "diff": ""},
        "team": {"members": [{"role": "lead"}, {"role": "coder"}], "runtime_source": "runtime_recommended"},
        "pending_approvals": [{"tool_name": "run_tests", "kind": "run_command"}],
    }

    result = validate_outcome(task, outcome, [])

    assert result["ok"] is False
    assert any("waiting for approval" in error for error in result["errors"])


def test_validate_mixed_task_requires_runtime_agent_activity():
    task = _task("tiny_frontend_mixed")
    outcome = {
        "thread_id": "run-c",
        "status": "completed",
        "strategy": "feature_delivery",
        "summary": {"final_message": "Updated frontend and README."},
        "stages": [{"id": "intake"}, {"id": "implement"}],
        "changes": {
            "files": [{"path": "README.md", "change_type": "modified"}],
            "diff": "diff --git a/README.md b/README.md",
        },
        "team": {
            "members": [{"role": "lead"}, {"role": "planner"}, {"role": "tester"}],
            "runtime_source": "runtime_recommended",
        },
    }

    failed = validate_outcome(task, outcome, [])
    passed = validate_outcome(task, outcome, [{"type": "parallel_agents_started"}])

    assert failed["ok"] is False
    assert any("agent activity" in error for error in failed["errors"])
    assert passed["ok"] is True


def test_prepare_workspace_copies_fixture(tmp_path):
    task = _task("tiny_frontend_mixed")
    workspace = prepare_workspace(task, tmp_path)

    assert (workspace / "README.md").exists()
    assert (workspace / "index.html").exists()
    assert (workspace / "src" / "main.js").exists()
    assert Path(workspace).name == "tiny_frontend_mixed"
