from pathlib import Path

from src.tools.git_tools import (
    auto_track_changes,
    ensure_git_repo,
    git_commit,
    git_diff,
    git_file_history,
    git_log,
    git_reset,
    git_status,
    set_git_workspace,
    get_git_workspace,
)


def test_git_reset_hard_requires_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_git_repo(workspace)
    target = workspace / "app.txt"
    target.write_text("stable\n", encoding="utf-8")
    git_commit(workspace, "Add app file")

    target.write_text("dirty\n", encoding="utf-8")
    warning = git_reset(workspace, mode="hard", ref="HEAD")

    assert "confirmed=true" in warning
    assert target.read_text(encoding="utf-8") == "dirty\n"

    result = git_reset(workspace, mode="hard", ref="HEAD", confirmed=True)

    assert "Hard reset" in result
    assert target.read_text(encoding="utf-8") == "stable\n"


# --- ensure_git_repo ---


def test_ensure_git_repo_initializes(tmp_path):
    workspace = tmp_path / "new_repo"
    workspace.mkdir()
    result = ensure_git_repo(workspace)
    assert "initialized" in result.lower() or result == "ok"
    assert (workspace / ".git").exists()


def test_ensure_git_repo_already_exists(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = ensure_git_repo(workspace)
    assert result == "ok"


# --- git_status ---


def test_git_status_clean(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_status(workspace)
    assert "clean" in result.lower() or "no changes" in result.lower()


def test_git_status_shows_modified(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    f = workspace / "file.txt"
    f.write_text("content", encoding="utf-8")
    from src.tools.git_tools import _run_git
    _run_git(["add", "file.txt"], workspace)
    _run_git(["commit", "-m", "add file"], workspace)

    f.write_text("modified", encoding="utf-8")
    result = git_status(workspace)
    assert "modified" in result.lower()


# --- git_diff ---


def test_git_diff_no_changes(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_diff(workspace)
    assert "no changes" in result.lower()


def test_git_diff_shows_changes(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    f = workspace / "file.txt"
    f.write_text("original", encoding="utf-8")
    from src.tools.git_tools import _run_git
    _run_git(["add", "file.txt"], workspace)
    _run_git(["commit", "-m", "add"], workspace)

    f.write_text("changed", encoding="utf-8")
    result = git_diff(workspace)
    assert "changed" in result


# --- git_commit ---


def test_git_commit_empty_message(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_commit(workspace, "")
    assert "required" in result.lower()


def test_git_commit_nothing_to_commit(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_commit(workspace, "test message")
    assert "nothing to commit" in result.lower()


def test_git_commit_success(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    f = workspace / "file.txt"
    f.write_text("content", encoding="utf-8")
    result = git_commit(workspace, "Add file")
    assert "committed" in result.lower()


# --- git_log ---


def test_git_log_shows_initial_commit(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_log(workspace)
    # ensure_git_repo creates an initial .gitignore commit
    assert "initial commit" in result.lower() or "recent commits" in result.lower()


def test_git_log_shows_commits(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    f = workspace / "file.txt"
    f.write_text("content", encoding="utf-8")
    git_commit(workspace, "First commit")
    result = git_log(workspace)
    assert "first commit" in result.lower() or "First commit" in result


# --- git_file_history ---


def test_git_file_history_no_commits(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_file_history(workspace, "nonexistent.txt")
    assert "no commits" in result.lower()


def test_git_file_history_shows_history(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    f = workspace / "file.txt"
    f.write_text("v1", encoding="utf-8")
    git_commit(workspace, "v1")
    f.write_text("v2", encoding="utf-8")
    git_commit(workspace, "v2")
    result = git_file_history(workspace, "file.txt")
    assert "file.txt" in result


# --- git_reset ---


def test_git_reset_unknown_mode(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = git_reset(workspace, mode="invalid")
    assert "error" in result.lower() or "unknown" in result.lower()


# --- auto_track_changes ---


def test_auto_track_changes_nothing_changed(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    result = auto_track_changes(workspace)
    assert result == ""


def test_auto_track_changes_commits(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    ensure_git_repo(workspace)
    f = workspace / "file.txt"
    f.write_text("content", encoding="utf-8")
    from src.tools.git_tools import _run_git
    _run_git(["add", "file.txt"], workspace)
    _run_git(["commit", "-m", "init"], workspace)

    f.write_text("updated", encoding="utf-8")
    result = auto_track_changes(workspace, description="update file")
    assert "committed" in result.lower()


# --- workspace management ---


def test_set_and_get_git_workspace(tmp_path):
    workspace = tmp_path / "my_workspace"
    workspace.mkdir()
    set_git_workspace(workspace)
    assert get_git_workspace() == workspace.resolve()
