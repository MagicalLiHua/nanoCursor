"""Real Git failure cases for worktree identity, retention and user deletion."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from nanocursor.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
from nanocursor.worktree.changes import count_worktree_changes, has_worktree_changes
from nanocursor.worktree.cleanup import cleanup_stale_worktrees
from nanocursor.worktree.manager import WorktreeError, WorktreeManager


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("NANOCURSOR_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("original")
    (repo / ".gitignore").write_text(".env\nnode_modules/\nignored/\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    return WorktreeManager(str(repo), removal_guard=lambda wt: None)


@pytest.mark.asyncio
async def test_existing_branch_never_reset_and_unknown_path_never_reused(manager):
    repo = Path(manager.repo_root)
    git(repo, "branch", "worktree-same")
    before = git(repo, "rev-parse", "worktree-same")
    tree = await manager.create("same")
    assert tree.branch != "worktree-same"
    assert git(repo, "rev-parse", "worktree-same") == before
    path = Path(manager.worktree_dir) / "alien"
    path.mkdir()
    (path / "artifact").write_text("keep")
    with pytest.raises(WorktreeError, match="already exists"):
        await manager.create("alien")
    assert (path / "artifact").read_text() == "keep"


@pytest.mark.asyncio
async def test_restart_preserves_creation_base_after_commits(manager):
    wt = await manager.create("restart")
    await manager.enter(wt.name)
    (Path(wt.path) / "file.txt").write_text("result")
    git(wt.path, "add", ".")
    git(wt.path, "commit", "-m", "result")
    restarted = WorktreeManager(manager.repo_root)
    session = restarted.restore_session()
    restored = restarted.active[wt.name]
    assert session.workspace_id == wt.workspace_id == restored.workspace_id
    assert restored.head_commit == wt.head_commit != git(wt.path, "rev-parse", "HEAD")
    assert count_worktree_changes(restored.path, restored.head_commit).new_commits == 1


@pytest.mark.asyncio
async def test_dirty_source_requires_explicit_committed_baseline(manager):
    repo = Path(manager.repo_root)
    before_head = git(repo, "rev-parse", "HEAD")
    (repo / "file.txt").write_text("my staged change")
    git(repo, "add", "file.txt")
    (repo / "file.txt").write_text("my unstaged change")
    (repo / "untracked.txt").write_text("my draft")
    before_status = git(repo, "status", "--porcelain")
    before_index = (repo / ".git" / "index").read_bytes()
    with pytest.raises(WorktreeError, match="without those changes"):
        await manager.create("dirty")
    assert not (Path(manager.worktree_dir) / "dirty").exists()
    wt = await manager.create("dirty", accept_committed_base=True)
    assert (Path(wt.path) / "file.txt").read_text() == "original"
    assert not (Path(wt.path) / "untracked.txt").exists()
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert (repo / ".git" / "index").read_bytes() == before_index
    assert set(before_status.splitlines()) <= set(git(repo, "status", "--porcelain").splitlines())


@pytest.mark.asyncio
async def test_default_setup_leaves_dependencies_env_and_git_config_alone(manager):
    repo = Path(manager.repo_root)
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "valuable").write_text("dependency")
    (repo / ".env").write_text("PRIVATE=secret")
    config = (repo / ".git" / "config").read_bytes()
    wt = await manager.create("independent")
    assert not (Path(wt.path) / "node_modules").exists()
    assert not (Path(wt.path) / ".env").exists()
    assert (repo / ".git" / "config").read_bytes() == config
    await manager.enter(wt.name)
    await manager.exit(wt.name)
    assert Path(wt.path).exists()


@pytest.mark.asyncio
async def test_source_status_does_not_refresh_user_index(manager):
    repo = Path(manager.repo_root)
    # Same bytes with a different inode/mtime would normally let git status update
    # the source index's stat cache. The manager's inspection is read-only.
    tracked = repo / "file.txt"
    tracked.unlink()
    tracked.write_text("original")
    before = (repo / ".git" / "index").read_bytes()
    await manager.create("read-only-source")
    assert (repo / ".git" / "index").read_bytes() == before


@pytest.mark.asyncio
async def test_explicit_allowlist_copies_selected_ignored_files(manager):
    repo = Path(manager.repo_root)
    (repo / ".env").write_text("allowed local config")
    (repo / ".worktreeinclude").write_text(".env\n")
    wt = await manager.create("config", accept_committed_base=True)
    assert (Path(wt.path) / ".env").read_text() == "allowed local config"


@pytest.mark.asyncio
async def test_managed_directory_symlink_cannot_redirect_creation(manager, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(manager.repo_root) / ".nanocursor").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorktreeError, match="must not be symbolic links"):
        await manager.create("redirected")
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_git_query_failure_is_unknown_not_clean(manager, monkeypatch):
    wt = await manager.create("unknown")
    monkeypatch.setattr("nanocursor.worktree.changes._run_git", lambda *a, **kw: subprocess.CompletedProcess(a, 128, "", "failed"))
    assert count_worktree_changes(wt.path, wt.head_commit).state == "unknown"
    assert has_worktree_changes(wt.path, wt.head_commit)
    with pytest.raises(WorktreeError, match="status unknown"):
        manager.prepare_removal(wt.name)
    assert (await manager.auto_cleanup(wt.name, wt.head_commit)).kept


@pytest.mark.asyncio
async def test_missing_baseline_unknown(manager):
    wt = await manager.create("baseline")
    assert count_worktree_changes(wt.path, "").state == "unknown"
    assert count_worktree_changes(wt.path, "bad-ref").state == "unknown"


@pytest.mark.asyncio
async def test_ignored_results_never_automatically_cleaned(manager):
    wt = await manager.create("agent-a1234567")
    artifact = Path(wt.path) / ".env"
    artifact.write_text("result")
    changes = count_worktree_changes(wt.path, wt.head_commit)
    assert changes.ignored == 1 and changes.state == "changed"
    assert (await manager.auto_cleanup(wt.name, wt.head_commit)).kept
    assert await cleanup_stale_worktrees(manager, -1) == 0
    assert artifact.read_text() == "result"


@pytest.mark.asyncio
async def test_model_discard_flag_is_not_user_authorization(manager):
    wt = await manager.create("protected")
    await manager.enter(wt.name)
    result = await ExitWorktreeTool(manager).execute(ExitWorktreeParams(action="remove", discard_changes=True))
    assert result.is_error and "user" in result.output
    with pytest.raises(WorktreeError, match="user confirmation"):
        await manager.exit(wt.name, action="remove", discard_changes=True)
    assert Path(wt.path).exists()


@pytest.mark.asyncio
async def test_removal_confirmation_binds_ignored_file_contents(manager):
    wt = await manager.create("binding")
    artifact = Path(wt.path) / ".env"
    artifact.write_text("first")
    approval = manager.prepare_removal(wt.name)
    artifact.write_text("later")
    with pytest.raises(WorktreeError, match="changed since confirmation"):
        await manager.exit(wt.name, action="remove", confirmation_token=approval.token)
    assert artifact.read_text() == "later"
    with pytest.raises(WorktreeError, match="user confirmation"):
        await manager.exit(wt.name, action="remove", confirmation_token=approval.token)


@pytest.mark.asyncio
async def test_remove_failure_preserves_branch_registration_and_session(manager, monkeypatch):
    wt = await manager.create("remove-fails")
    await manager.enter(wt.name)
    approval = manager.prepare_removal(wt.name)
    original = manager._run_git
    calls = []
    def fail_remove(args, cwd=None):
        calls.append(args)
        if args[:2] == ["worktree", "remove"]:
            return subprocess.CompletedProcess(args, 1, "", "busy")
        return original(args, cwd)
    monkeypatch.setattr(manager, "_run_git", fail_remove)
    with pytest.raises(WorktreeError, match="Removal failed"):
        await manager.exit(wt.name, action="remove", confirmation_token=approval.token)
    assert wt.name in manager.active and manager.current_session
    assert Path(wt.path).exists() and git(manager.repo_root, "rev-parse", wt.branch)
    assert not any(args[0] in {"branch", "update-ref"} for args in calls)


@pytest.mark.asyncio
async def test_branch_delete_failure_records_partial_completion(manager, monkeypatch):
    wt = await manager.create("branch-fails")
    await manager.enter(wt.name)
    approval = manager.prepare_removal(wt.name)
    original = manager._run_git
    def fail_branch(args, cwd=None):
        if args[0] == "update-ref":
            return subprocess.CompletedProcess(args, 1, "", "locked")
        return original(args, cwd)
    monkeypatch.setattr(manager, "_run_git", fail_branch)
    with pytest.raises(WorktreeError, match="partial removal"):
        await manager.exit(wt.name, action="remove", confirmation_token=approval.token)
    assert not Path(wt.path).exists()
    assert manager.current_session is None
    assert manager.active[wt.name].state == "removal_incomplete"
    assert git(manager.repo_root, "rev-parse", wt.branch)


@pytest.mark.asyncio
async def test_setup_failure_keeps_creation_transaction(manager, monkeypatch):
    def fail(*args):
        raise OSError("injected setup failure")
    monkeypatch.setattr("nanocursor.worktree.manager.perform_post_creation_setup", fail)
    with pytest.raises(WorktreeError, match="retained"):
        await manager.create("incomplete")
    restarted = WorktreeManager(manager.repo_root)
    wt = restarted.active["incomplete"]
    assert wt.state == "creation_incomplete" and wt.head_commit
    assert Path(wt.path).exists()
    with pytest.raises(WorktreeError, match="requires inspection"):
        await restarted.enter(wt.name)


@pytest.mark.asyncio
async def test_delete_requires_owner_guard_and_rechecks_it(manager):
    wt = await manager.create("owner")
    approval = manager.prepare_removal(wt.name)
    manager.removal_guard = None
    with pytest.raises(WorktreeError, match="recovery/owner checks"):
        await manager.exit(wt.name, action="remove", confirmation_token=approval.token)
    assert Path(wt.path).exists()


@pytest.mark.asyncio
async def test_new_checkout_same_path_has_distinct_workspace_identity(manager):
    old = await manager.create("reuse")
    approval = manager.prepare_removal(old.name)
    await manager.exit(old.name, action="remove", confirmation_token=approval.token)
    new = await manager.create("reuse")
    assert old.path == new.path
    assert old.workspace_id != new.workspace_id
    assert old.creation_id != new.creation_id and old.branch != new.branch


@pytest.mark.asyncio
async def test_pinned_checkpoint_blocks_delete_but_unpinned_history_does_not(manager):
    from nanocursor.filehistory import FileHistory
    wt = await manager.create("checkpoint")
    history = FileHistory(wt.path, "session", store=manager.registry.store, workspace_id=wt.workspace_id)
    checkpoint = history.begin_checkpoint(0, "task")
    history.pin_checkpoint(checkpoint.checkpoint_id)
    with pytest.raises(WorktreeError, match="pinned checkpoint"):
        manager.prepare_removal(wt.name)
    history.pin_checkpoint(checkpoint.checkpoint_id, pinned=False)
    approval = manager.prepare_removal(wt.name)
    await manager.exit(wt.name, action="remove", confirmation_token=approval.token)
    assert history.get_snapshots()[0].checkpoint_id == checkpoint.checkpoint_id


def test_creation_killed_after_git_add_retains_exact_base_and_registration(manager):
    import signal
    script = """
import asyncio, os, signal, sys
from nanocursor.worktree.manager import WorktreeManager
class InterruptedManager(WorktreeManager):
    def _git_value(self, args, cwd=None):
        result = super()._git_value(args, cwd)
        if args[:2] == ['worktree', 'add']:
            os.kill(os.getpid(), signal.SIGKILL)
        return result
asyncio.run(InterruptedManager(sys.argv[1]).create('killed'))
"""
    expected_base = git(manager.repo_root, "rev-parse", "HEAD")
    result = subprocess.run([sys.executable, "-c", script, manager.repo_root], capture_output=True, text=True)
    assert result.returncode == -signal.SIGKILL
    path = Path(manager.worktree_dir) / "killed"
    (path / "file.txt").write_text("result after interruption")
    git(path, "add", "file.txt")
    git(path, "commit", "-m", "preserved work")
    restarted = WorktreeManager(manager.repo_root)
    record = restarted.active["killed"]
    assert record.state == "creating" and record.head_commit == expected_base
    assert record.head_commit != git(path, "rev-parse", "HEAD")
    assert (path / "file.txt").read_text() == "result after interruption"


@pytest.mark.parametrize("accept", [False, True])
def test_cli_worktree_dirty_baseline_is_explicit_and_preserves_source(manager, monkeypatch, capsys, accept):
    from nanocursor import __main__ as cli
    from nanocursor.config import AppConfig, ProviderConfig
    repo = Path(manager.repo_root)
    (repo / "file.txt").write_text("user draft")
    (repo / "untracked.txt").write_text("notes")
    before_index = (repo / ".git" / "index").read_bytes()
    before_head = git(repo, "rev-parse", "HEAD")
    config = AppConfig([ProviderConfig("fake", "openai-compat", "https://example.invalid", "fake", auth="none")])
    monkeypatch.setattr(cli, "load_config", lambda **kwargs: config)
    captured = []
    async def fake_prompt(*args, workspace=None, **kwargs):
        captured.append(workspace)
        return 0
    monkeypatch.setattr(cli, "_run_prompt", fake_prompt)
    monkeypatch.chdir(repo)
    args = ["nanocursor", "-p", "task", "--worktree", "demo"]
    if accept:
        args.append("--worktree-from-commit")
    monkeypatch.setattr(sys, "argv", args)
    if accept:
        cli.main()
    else:
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 1
    assert Path.cwd() == repo
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert (repo / ".git" / "index").read_bytes() == before_index
    assert (repo / "file.txt").read_text() == "user draft"
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    if accept:
        assert captured and captured[0].active_cwd != repo
        assert (captured[0].active_cwd / "file.txt").read_text() == "original"
        assert not (captured[0].active_cwd / "untracked.txt").exists()
        assert before_head in output.err
    else:
        assert not captured and "--worktree-from-commit" in output.err
        assert not manager.registry.store.rows("SELECT * FROM operations")


def test_cli_unknown_worktree_error_is_readable(manager, monkeypatch, capsys):
    from nanocursor import __main__ as cli
    from nanocursor.config import AppConfig, ProviderConfig
    config = AppConfig([ProviderConfig("fake", "openai-compat", "https://example.invalid", "fake", auth="none")])
    monkeypatch.setattr(cli, "load_config", lambda **kwargs: config)
    monkeypatch.chdir(manager.repo_root)
    monkeypatch.setattr(sys, "argv", ["nanocursor", "-p", "task", "--worktree", "../invalid"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    assert "Cannot start in Worktree" in capsys.readouterr().err
