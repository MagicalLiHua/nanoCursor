"""Git runner boundary tests."""

from __future__ import annotations

import asyncio

from src.runtime.git_runner import run_git, run_git_async


def test_run_git_returns_completed_process(tmp_path):
    result = run_git(tmp_path, ["status", "--short"])

    assert result.args[:3] == ["git", "-C", str(tmp_path.resolve())]
    assert isinstance(result.returncode, int)
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


def test_run_git_async_returns_completed_process(tmp_path):
    result = asyncio.run(run_git_async(tmp_path, ["status", "--short"]))

    assert result.args[:3] == ["git", "-C", str(tmp_path.resolve())]
    assert isinstance(result.returncode, int)
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


def test_run_git_handles_missing_binary(monkeypatch, tmp_path):
    from src.runtime import git_runner

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(git_runner.subprocess, "run", raise_missing)

    result = run_git(tmp_path, ["status"])

    assert result.returncode == -1
    assert "git missing" in result.stderr
