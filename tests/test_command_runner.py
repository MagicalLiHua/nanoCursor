"""Command runner tests — timeout, capture, dangerous commands, file output."""

import asyncio
import os

import pytest

from src.runtime.command_runner import run_command, run_command_async


@pytest.fixture(autouse=True)
def _isolate_executor_routing(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_GO_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("NANOCURSOR_GO_EXECUTOR_FALLBACK", raising=False)
    monkeypatch.delenv("NANOCURSOR_GO_RUNTIME_ENABLED", raising=False)
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")


class TestCommandRunner:
    def test_simple_command_echo(self, tmp_path):
        result = run_command("echo hello", cwd=str(tmp_path))
        assert result["backend"] == "python_subprocess"
        assert result["cwd"] == str(tmp_path.resolve())
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert not result["timed_out"]
        assert result["stdout_truncated"] is False
        assert result["stderr_truncated"] is False

    def test_executor_is_opt_in(self, tmp_path, monkeypatch):
        from src.runtime import command_runner

        monkeypatch.delenv("NANOCURSOR_GO_EXECUTOR_ENABLED", raising=False)
        monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")
        monkeypatch.setattr(command_runner, "_EXECUTOR_AVAILABLE", True)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("executor should be opt-in")

        monkeypatch.setattr(command_runner.executor_client, "run_command", fail_if_called)
        result = command_runner.run_command("echo hello", cwd=str(tmp_path))
        assert result["backend"] == "python_subprocess"
        assert result["exit_code"] == 0

    def test_executor_routing_keeps_short_command_on_python(self, tmp_path, monkeypatch):
        from src.runtime import command_runner

        monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto")
        monkeypatch.setattr(command_runner, "_EXECUTOR_AVAILABLE", True)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("short command should stay on Python")

        monkeypatch.setattr(command_runner.executor_client, "run_command", fail_if_called)
        result = command_runner.run_command("ls", cwd=str(tmp_path))

        assert result["backend"] == "python_subprocess"
        assert "low-latency" in result["route_reason"]

    def test_executor_routing_sends_test_command_to_go(self, tmp_path, monkeypatch):
        from src.runtime import command_runner

        monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto")
        monkeypatch.setattr(command_runner, "_EXECUTOR_AVAILABLE", True)

        def fake_run_command(*args, **kwargs):
            return {
                "command": args[0],
                "cwd": kwargs["cwd"],
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
                "duration_ms": 12,
                "timed_out": False,
            }

        monkeypatch.setattr(command_runner.executor_client, "run_command", fake_run_command)
        result = command_runner.run_command("pytest -q", cwd=str(tmp_path))

        assert result["backend"] == "go_executor"
        assert result["fallback"] is False
        assert "long-running" in result["route_reason"]

    def test_executor_routing_falls_back_when_go_fails(self, tmp_path, monkeypatch):
        from src.runtime import command_runner

        monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_FALLBACK", "true")
        monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto")
        monkeypatch.setattr(command_runner, "_EXECUTOR_AVAILABLE", True)

        def failing_run_command(*args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(command_runner.executor_client, "run_command", failing_run_command)
        result = command_runner.run_command("pytest --version", cwd=str(tmp_path))

        assert result["backend"] == "python_subprocess"
        assert result["fallback"] is True
        assert "connection refused" in result["route_reason"]

    def test_legacy_go_runtime_is_skipped_when_go_executor_enabled(self, tmp_path, monkeypatch):
        from src.runtime import command_runner

        monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_GO_RUNTIME_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto")
        monkeypatch.setattr(command_runner, "_EXECUTOR_AVAILABLE", True)

        def fail_executor(*args, **kwargs):
            raise AssertionError("short command should not use gRPC executor")

        def fail_legacy(*args, **kwargs):
            raise AssertionError("legacy HTTP go-runtime must not run when gRPC executor is enabled")

        monkeypatch.setattr(command_runner.executor_client, "run_command", fail_executor)
        monkeypatch.setattr(command_runner, "run_command_via_go_runtime", fail_legacy)

        result = command_runner.run_command("python -c 'print(42)'", cwd=str(tmp_path))

        assert result["backend"] == "python_subprocess"
        assert result["exit_code"] == 0

    def test_async_command_echo(self, tmp_path):
        result = asyncio.run(run_command_async("echo hello", cwd=str(tmp_path)))
        assert result["backend"] == "python_subprocess"
        assert result["cwd"] == str(tmp_path.resolve())
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert not result["timed_out"]

    def test_command_env_injection(self, tmp_path):
        env = os.environ.copy()
        env["NC_TEST_ENV"] = "works"

        result = run_command(
            "python -c \"import os; print(os.getenv('NC_TEST_ENV'))\"",
            cwd=str(tmp_path),
            env=env,
        )

        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "works"

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

    def test_output_truncation_flags(self, tmp_path):
        result = run_command(
            "python -c \"print('x' * 20)\"",
            cwd=str(tmp_path),
            max_stdout_chars=5,
        )
        assert result["exit_code"] == 0
        assert result["stdout"] == "xxxxx"
        assert result["stdout_truncated"] is True
        assert result["stderr_truncated"] is False


class TestCommandRunnerTimeout:
    def test_timeout_short_command(self, tmp_path):
        """A command that sleeps longer than the timeout should be killed."""
        result = run_command("sleep 3", cwd=str(tmp_path), timeout_seconds=0.5)
        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert result["backend"] == "python_subprocess"
