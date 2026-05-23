import subprocess

from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store


def test_run_diff_falls_back_to_file_events_for_git_ignored_workspace(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    (repo / ".gitignore").write_text(".nanocursor/\n", encoding="utf-8")

    workspace = repo / ".nanocursor" / "workspaces" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    thread_id = "run_diff_events"
    get_event_store().append_event(
        thread_id,
        "file_changed",
        title="文件变更：calc.py",
        content="Created calc.py (31 bytes)",
        agent="coder",
        payload={"path": "calc.py", "change_type": "modified", "output": "Created calc.py (31 bytes)"},
        workspace_dir=str(workspace),
    )

    diff = get_run_diff(thread_id, str(workspace))

    assert diff["source"] == "events"
    assert diff["changed_files"] == [
        {"path": "calc.py", "status": "event", "change_type": "created"}
    ]
