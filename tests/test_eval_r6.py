"""R6 eval tests — enhanced scoring, artifacts, compare, suite, 10+ tasks."""

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.services.eval_service import (
    get_eval_artifacts,
    get_eval_task,
    list_evals,
    score_eval_run,
)
from src.api.services.eval_runner_service import (
    get_eval_summary,
    run_eval_suite,
)


class TestEvalTasks:
    def test_all_tasks_have_required_fields(self):
        tasks = list_evals()
        assert len(tasks) >= 2
        for t in tasks:
            assert "id" in t
            assert "prompt" in t
            assert "category" in t

    def test_get_existing_task(self):
        task = get_eval_task("bug_fix_import_error")
        assert task is not None
        assert task["difficulty"] == "medium"

    def test_get_nonexistent_task(self):
        assert get_eval_task("nonexistent_eval_task_xyz") is None

    def test_all_categories_present(self):
        tasks = list_evals()
        categories = {t.get("category") for t in tasks}
        assert len(categories) >= 3

    def test_at_least_10_tasks(self):
        tasks = list_evals()
        assert len(tasks) >= 10, f"Expected >=10 tasks, got {len(tasks)}"


class TestEvalScoring:
    def test_score_with_events(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"score_{uuid.uuid4().hex[:8]}"
        store.create_session(thread_id, "test", str(ws), status="completed")
        store.append_event(thread_id, "plan_created", "plan", "ok", workspace_dir=str(ws))
        store.append_event(thread_id, "tool_call_finished", "tool", "ok", agent="coder",
                           payload={"tool": "write_file", "output": "ok"}, workspace_dir=str(ws))
        store.append_event(thread_id, "file_changed", "file", "x.py", agent="coder",
                           payload={"path": "x.py"}, workspace_dir=str(ws))
        store.append_event(thread_id, "test_finished", "test", "passed", agent="tester",
                           payload={"status": "passed"}, workspace_dir=str(ws))
        store.append_event(thread_id, "done", "done", "ok", workspace_dir=str(ws))

        score = score_eval_run(thread_id, str(ws), {
            "required_events": ["plan_created", "done"],
            "tests_pass": True,
            "max_changed_files": 3,
        })
        assert score["overall"] == "passed"
        assert score["tool_call_count"] == 1
        assert score["file_write_count"] == 1
        assert "delivery_generated" in score
        assert "error_count" in score

    def test_score_failed_tests(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"fail_{uuid.uuid4().hex[:8]}"
        store.create_session(thread_id, "test", str(ws), status="completed")
        store.append_event(thread_id, "test_finished", "test", "failed", agent="tester",
                           payload={"status": "failed"}, workspace_dir=str(ws))
        store.append_event(thread_id, "done", "done", "ok", workspace_dir=str(ws))

        score = score_eval_run(thread_id, str(ws), {
            "required_events": ["done"],
            "tests_pass": True,
        })
        test_check = next((c for c in score["checks"] if c["id"] == "tests_pass"), None)
        assert test_check is not None
        assert test_check["status"] == "failed"


class TestEvalSuite:
    def test_suite_missing_eval(self):
        result = run_eval_suite(["nonexistent_eval"])
        assert "error" in result["results"][0]

    def test_suite_empty(self):
        result = run_eval_suite([])
        assert result["total"] == 0

    def test_summary(self):
        summary = get_eval_summary()
        assert "total_runs" in summary
        assert "pass_rate" in summary


class TestEvalArtifacts:
    def test_artifacts_404(self):
        from src.api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/evals/runs/nonexistent_xyz/artifacts")
        assert resp.status_code == 404

    def test_compare_endpoint(self):
        from src.api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/evals/runs/run_a_xyz/compare?other_run_id=run_b_xyz")
        assert resp.status_code == 404


class TestEvalAPI:
    def test_list_evals(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.get("/api/evals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["evals"]) >= 10

    def test_get_eval_404(self):
        from src.api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/evals/nonexistent_eval")
        assert resp.status_code == 404

    def test_suite_run(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.post("/api/evals/suite/run", json={
            "eval_ids": ["fix_test_failure"],
            "mode": "command_only",
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_summary_endpoint(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.get("/api/evals/summary")
        assert resp.status_code == 200

    def test_eval_run_404(self):
        from src.api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/evals/runs/bad_id")
        assert resp.status_code == 404

    def test_eval_detail_exists(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.get("/api/evals/bug_fix_import_error")
        assert resp.status_code == 200
        assert resp.json()["id"] == "bug_fix_import_error"
