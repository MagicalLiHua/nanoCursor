from src.tools.git_tools import ensure_git_repo, git_commit, git_reset


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
