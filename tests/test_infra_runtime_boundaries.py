"""Infra runtime boundary tests."""

from __future__ import annotations

import subprocess

from src.infra.hooks import HookManager
from src.infra.worktree import WorktreeManager


def test_hook_manager_command_uses_runtime_runner(tmp_path, monkeypatch):
    hooks_dir = tmp_path / ".hooks"
    config_file = tmp_path / ".hooks.json"
    trust_file = tmp_path / ".claude" / ".claude_trusted"
    trust_file.parent.mkdir(parents=True)
    trust_file.write_text("yes", encoding="utf-8")
    config_file.write_text(
        '{"hooks":{"SessionStart":[{"type":"command","command":"echo \'{\\"exit_code\\":2,\\"messages\\":[\\"ok\\"]}\'"}]}}',
        encoding="utf-8",
    )

    from src.infra import hooks as hooks_module

    monkeypatch.setattr(hooks_module, "WORKDIR", tmp_path)
    manager = HookManager(hooks_dir=hooks_dir)

    blocked, messages, context = manager.run_hooks("SessionStart", {"tool_name": "read_file", "tool_input": {}})

    assert blocked is False
    assert messages == ["ok"]
    assert context["tool_name"] == "read_file"


def test_worktree_manager_git_available_uses_git_runner(monkeypatch, tmp_path):
    from src.infra import worktree as worktree_module

    calls = []

    def fake_run_git(workspace, args, *, timeout_seconds=10):
        calls.append((workspace, args, timeout_seconds))
        return subprocess.CompletedProcess(["git"], 0, stdout="", stderr="")

    monkeypatch.setattr(worktree_module, "WORKDIR", tmp_path)
    monkeypatch.setattr(worktree_module, "WORKTREES_DIR", tmp_path / ".worktrees")
    monkeypatch.setattr(worktree_module, "INDEX_FILE", tmp_path / ".worktrees" / "index.json")
    monkeypatch.setattr(worktree_module, "EVENTS_FILE", tmp_path / ".worktrees" / "events.jsonl")
    monkeypatch.setattr(worktree_module, "run_git", fake_run_git)

    manager = WorktreeManager()

    assert manager._git_available() is True
    assert calls == [(tmp_path, ["status"], 5)]
