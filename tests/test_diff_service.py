import subprocess

from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store


def _init_repo(repo):
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)


def _commit(repo, message="initial"):
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, capture_output=True)


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
    assert len(diff["changed_files"]) == 1
    assert diff["changed_files"][0]["path"] == "calc.py"
    assert diff["changed_files"][0]["status"] == "event"
    assert diff["changed_files"][0]["change_type"] == "created"
    assert diff["changed_files"][0]["additions"] == 2
    assert "diff --git a/calc.py b/calc.py" in diff["diff"]
    assert "+def add(a, b):" in diff["diff"]


def test_run_diff_recovers_fenced_diff_from_file_event(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    thread_id = "run_diff_event_patch"
    get_event_store().append_event(
        thread_id,
        "file_changed",
        title="文件变更：calc.py",
        content="Edited calc.py",
        agent="coder",
        payload={
            "path": "calc.py",
            "change_type": "modified",
            "output": """Edited calc.py
```diff
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
-def add(a, b):
-    return a + b
+def add(*args):
+    return sum(args)
```""",
        },
        workspace_dir=str(workspace),
    )

    diff = get_run_diff(thread_id, str(workspace))

    assert diff["source"] == "events"
    assert "+++ b/calc.py" in diff["diff"]
    assert diff["changed_files"][0]["path"] == "calc.py"
    assert diff["changed_files"][0]["additions"] == 2
    assert diff["changed_files"][0]["deletions"] == 2


def test_run_diff_includes_untracked_new_file_patch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _commit(repo)
    (repo / "src").mkdir()
    (repo / "src" / "new_tool.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

    diff = get_run_diff("run_untracked_patch", str(repo))

    assert {"path": "src/new_tool.py", "status": "??", "change_type": "created"} in diff["changed_files"]
    assert "diff --git a/src/new_tool.py b/src/new_tool.py" in diff["diff"]
    assert "new file mode 100644" in diff["diff"]
    assert "+def hello():" in diff["diff"]


def test_run_diff_includes_staged_new_file_patch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _commit(repo)
    (repo / "added.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "added.py"], cwd=repo, capture_output=True)

    diff = get_run_diff("run_staged_patch", str(repo))

    assert {"path": "added.py", "status": "A", "change_type": "created"} in diff["changed_files"]
    assert "diff --git a/added.py b/added.py" in diff["diff"]
    assert "+VALUE = 1" in diff["diff"]


def test_run_diff_reports_deleted_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "old.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit(repo)
    (repo / "old.py").unlink()

    diff = get_run_diff("run_deleted", str(repo))

    assert {"path": "old.py", "status": "D", "change_type": "deleted"} in diff["changed_files"]
    assert "deleted file mode" in diff["diff"]
    assert "--- a/old.py" in diff["diff"]


def test_run_diff_reports_staged_rename(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "old_name.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit(repo)
    subprocess.run(["git", "mv", "old_name.py", "new_name.py"], cwd=repo, capture_output=True)

    diff = get_run_diff("run_renamed", str(repo))

    assert {
        "path": "new_name.py",
        "old_path": "old_name.py",
        "status": "R",
        "change_type": "renamed",
    } in diff["changed_files"]
    assert "rename from old_name.py" in diff["diff"]
    assert "rename to new_name.py" in diff["diff"]


def test_run_diff_synthesizes_binary_untracked_patch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _commit(repo)
    (repo / "image.bin").write_bytes(b"\x00\x01\x02nanoCursor")

    diff = get_run_diff("run_binary", str(repo))

    assert {"path": "image.bin", "status": "??", "change_type": "created", "binary": True} in diff["changed_files"]
    assert "Binary files /dev/null and b/image.bin differ" in diff["diff"]


def test_run_diff_ignores_internal_runtime_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _commit(repo)
    (repo / ".nanocursor" / "runs" / "x").mkdir(parents=True)
    (repo / ".nanocursor" / "runs" / "x" / "session.json").write_text("{}", encoding="utf-8")

    diff = get_run_diff("run_internal", str(repo))

    assert diff["changed_files"] == []


def test_run_diff_ignores_generated_python_cache_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _commit(repo)
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"\0pyc")
    (repo / "tests" / "__pycache__").mkdir(parents=True)
    (repo / "tests" / "__pycache__" / "test_module.cpython-312.pyc").write_bytes(b"\0pyc")

    diff = get_run_diff("run_generated_cache", str(repo))

    assert diff["changed_files"] == []
    assert "__pycache__" not in diff["diff"]
