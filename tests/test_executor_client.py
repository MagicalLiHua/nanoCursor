"""Tests for executor gRPC client -- requires go-executor running on localhost:50055."""

import os
import time
import pytest

EXECUTOR_ADDR = os.getenv("NANOCURSOR_EXECUTOR_ADDR", "localhost:50055")


def executor_available():
    try:
        from src.runtime.executor_client import health
        result = health()
        return result.get("ok", False)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not executor_available(), reason="go-executor not running")


class TestExecutorHealth:
    def test_health(self):
        from src.runtime.executor_client import health
        result = health()
        assert result["ok"] is True
        assert result["service"] == "nanocursor-executor"


class TestExecutorPreview:
    def test_preview_safe(self):
        from src.runtime.executor_client import preview
        result = preview("echo hello", cwd="/tmp", workspace_dir="/tmp")
        assert result["allowed"] is True
        assert result["permission_level"] == "shell_safe"

    def test_preview_risky_denied(self):
        from src.runtime.executor_client import preview
        result = preview("rm -rf /tmp/test", cwd="/tmp", workspace_dir="/tmp")
        assert result["allowed"] is False
        assert result["error_code"] == "approval_required"

    def test_preview_workspace_violation(self):
        from src.runtime.executor_client import preview
        result = preview("echo hello", cwd="/etc", workspace_dir="/tmp")
        assert result["allowed"] is False
        assert result["error_code"] == "workspace_boundary_violation"


class TestExecutorExecute:
    def test_execute_echo(self):
        from src.runtime.executor_client import run_command
        result = run_command("echo hello", cwd="/tmp", workspace_dir="/tmp",
                             timeout_ms=5000, permission_level="shell_safe")
        assert result["status"] == "completed"
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_execute_denied(self):
        from src.runtime.executor_client import execute
        result = execute("rm -rf /tmp/test", cwd="/tmp", workspace_dir="/tmp")
        assert result["status"] == "denied"
        assert result["error_code"] == "approval_required"

    def test_cancel_run(self):
        from src.runtime.executor_client import execute, cancel, get_tool_run
        run = execute("sleep 10", cwd="/tmp", workspace_dir="/tmp",
                       timeout_ms=15000, permission_level="shell_safe")
        assert run["status"] == "running"

        result = cancel(run["id"])
        assert result["success"] is True

        time.sleep(0.3)
        final = get_tool_run(run["id"])
        assert final["status"] in ("cancelled", "failed")
