"""Real eval runner tests — command execution, suite run, summary, history."""

import json

import pytest
from fastapi.testclient import TestClient

from src.api.services.eval_runner_service import (
    run_eval_with_command,
    run_eval_suite,
    get_eval_summary,
)
from src.api.services.eval_service import get_eval_task, load_eval_tasks


class TestEvalRunnerReal:
    def test_run_eval_with_fixture_and_test_command(self, tmp_path):
        """Run eval task that has a fixture and test_command."""
        ws = tmp_path / "ws"
        ws.mkdir()
        # Use the bug_fix_import_error task which has fixture+test_command
        task = get_eval_task("bug_fix_import_error")
        if not task or not task.get("fixture") or not task.get("test_command"):
            pytest.skip("Eval task not configured with fixture + test_command")

        try:
            result = run_eval_with_command("bug_fix_import_error", str(ws), mode="command_only")
        except ValueError as exc:
            # Fixture may not exist in test context
            if "fixture" in str(exc).lower():
                pytest.skip(f"Fixture not available in test: {exc}")
            raise

        assert result["eval_id"] == "bug_fix_import_error"
        assert "score" in result
        assert "test_result" in result
        # test_result should have exit_code
        tr = result["test_result"]
        assert tr is not None
        assert "exit_code" in tr

    def test_command_only_mode_skips_agent_events(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        task = get_eval_task("bug_fix_import_error")
        if not task or not task.get("fixture") or not task.get("test_command"):
            pytest.skip("Eval task not configured")

        try:
            result = run_eval_with_command("bug_fix_import_error", str(ws), mode="command_only")
        except ValueError as exc:
            if "fixture" in str(exc).lower():
                pytest.skip(f"Fixture not available: {exc}")
            raise

        assert result["mode"] == "command_only"

    def test_agent_mode_includes_events(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        task = get_eval_task("bug_fix_import_error")
        if not task or not task.get("fixture") or not task.get("test_command"):
            pytest.skip("Eval task not configured")

        try:
            result = run_eval_with_command("bug_fix_import_error", str(ws), mode="agent")
        except ValueError as exc:
            if "fixture" in str(exc).lower():
                pytest.skip(f"Fixture not available: {exc}")
            raise

        assert result["mode"] == "agent"
        assert result["event_count"] > 0

    def test_run_nonexistent_eval_fails(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(ValueError, match="不存在"):
            run_eval_with_command("nonexistent_eval_xyz", str(ws))


class TestEvalSuite:
    def test_suite_run_multiple_tasks(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        # Use built-in starter evals
        result = run_eval_suite(
            ["todo_web_app", "bug_fix_import_error"],
            str(ws),
            mode="agent",
            stop_on_failure=False,
        )
        # todo_web_app always works, bug_fix may skip
        assert result["total"] >= 1
        assert "pass_rate" in result
        assert "results" in result

    def test_suite_empty_ids_returns_empty(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        result = run_eval_suite([], str(ws))
        assert result["total"] == 0


class TestEvalSummary:
    def test_summary_empty_workspace(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        summary = get_eval_summary(str(ws))
        assert summary["total_runs"] == 0
        assert "by_eval" in summary

    def test_summary_after_run(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        # Run one eval, then check summary
        try:
            run_eval_with_command("bug_fix_import_error", str(ws), mode="command_only")
        except ValueError:
            pytest.skip("Fixture not available")
        summary = get_eval_summary(str(ws))
        assert summary["total_runs"] >= 1


class TestEvalAPI:
    def test_suite_run_api(self):
        from api_server import app
        client = TestClient(app)
        resp = client.post("/api/evals/suite/run", json={
            "eval_ids": ["todo_web_app"],
            "mode": "agent",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "pass_rate" in data

    def test_summary_api(self):
        from api_server import app
        client = TestClient(app)
        resp = client.get("/api/evals/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "pass_rate" in data

    def test_eval_run_with_mode_param(self):
        from api_server import app
        client = TestClient(app)
        resp = client.post("/api/evals/todo_web_app/run?mode=agent")
        assert resp.status_code == 200
