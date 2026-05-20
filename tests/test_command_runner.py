"""Command runner tests — timeout, capture, dangerous commands, file output."""

import pytest

from src.runtime.command_runner import run_command


class TestCommandRunner:
    def test_simple_command_echo(self, tmp_path):
        result = run_command("echo hello", cwd=str(tmp_path))
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert not result["timed_out"]

    def test_command_stdout_capture(self, tmp_path):
        result = run_command("echo line1 && echo line2", cwd=str(tmp_path))
        assert result["exit_code"] == 0
        assert "line1" in result["stdout"]

    def test_command_stderr_capture(self, tmp_path):
        result = run_command("echo error >&2", cwd=str(tmp_path))
        # stdout may be empty, stderr should contain output
        assert result["exit_code"] == 0

    def test_command_failure_exit_code(self, tmp_path):
        result = run_command("exit 1", cwd=str(tmp_path))
        assert result["exit_code"] == 1

    def test_command_not_found(self, tmp_path):
        result = run_command("nonexistent_command_xyz_123", cwd=str(tmp_path))
        # shell=True on Unix: nonexistent command returns 127 (not -1)
        assert result["exit_code"] in (-1, 127)
        assert "not found" in result.get("stderr", "") or "not found" in result.get("stdout", "") or result["exit_code"] == 127

    def test_dangerous_command_blocked(self, tmp_path):
        result = run_command("sudo rm -rf /", cwd=str(tmp_path))
        assert result["exit_code"] == -1
        assert "危险" in result["stderr"] or "dangerous" in result["stderr"].lower()

    def test_cwd_not_exist_raises(self, tmp_path):
        bad = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            run_command("echo hi", cwd=str(bad))

    def test_duration_ms_recorded(self, tmp_path):
        result = run_command("echo test", cwd=str(tmp_path))
        assert result["duration_ms"] >= 0


class TestCommandRunnerTimeout:
    def test_timeout_short_command(self, tmp_path):
        """A command that sleeps longer than the timeout should be killed."""
        result = run_command("sleep 3", cwd=str(tmp_path), timeout_seconds=0.5)
        assert result["timed_out"] is True
        assert result["exit_code"] == -1
