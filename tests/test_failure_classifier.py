"""Failure classifier and remediation planner tests."""

import json
import uuid
from pathlib import Path

from src.api.services.failure_classifier_service import (
    FailureClass,
    FailureRecord,
    SuggestedAction,
    _can_auto_retry,
    _suggest_actions_for,
    classify_failure,
    classify_failure_typed,
    classify_run_failures,
    load_failures,
    save_failures,
)
from src.api.services.remediation_planner_service import (
    create_remediation_run,
    plan_remediation,
)


class TestFailureClassEnum:
    def test_all_values(self):
        values = {fc.value for fc in FailureClass}
        assert "environment_error" in values
        assert "command_error" in values
        assert "test_failure" in values
        assert "tool_policy_blocked" in values
        assert "approval_rejected" in values
        assert "approval_timeout" in values
        assert "workspace_error" in values
        assert "model_error" in values
        assert "patch_error" in values
        assert "unknown_error" in values


class TestFailureRecordModel:
    def test_minimal(self):
        fr = FailureRecord(
            failure_id="f1",
            thread_id="r1",
            failure_class=FailureClass.UNKNOWN_ERROR,
            title="test",
        )
        d = fr.model_dump()
        assert d["failure_class"] == "unknown_error"
        assert d["can_auto_retry"] is False
        assert d["evidence"] == {}


class TestLegacyClassifier:
    def test_syntax_error(self):
        r = classify_failure("SyntaxError: invalid syntax at line 10")
        assert r["category"] == "syntax_error"
        assert r["confidence"] == "high"

    def test_test_failure(self):
        r = classify_failure("FAILED: test_login - AssertionError")
        assert r["category"] == "test_failure"

    def test_unknown(self):
        r = classify_failure("some random message")
        assert r["category"] == "unknown"

    def test_with_context(self):
        r = classify_failure("something", context={"stage_id": "step_1"})
        assert "step_1" in r["summary"]


class TestTypedClassifier:
    def test_environment_error(self):
        assert classify_failure_typed("ModuleNotFoundError: No module named 'requests'") == FailureClass.ENVIRONMENT_ERROR

    def test_test_failure(self):
        assert classify_failure_typed("AssertionError: assert 1 == 2") == FailureClass.TEST_FAILURE

    def test_command_error(self):
        assert classify_failure_typed("command returned non-zero exit code 1") == FailureClass.COMMAND_ERROR

    def test_model_error(self):
        assert classify_failure_typed("RateLimitError: too many requests") == FailureClass.MODEL_ERROR

    def test_workspace_error(self):
        assert classify_failure_typed("Permission denied: /etc/passwd") == FailureClass.WORKSPACE_ERROR

    def test_patch_error(self):
        assert classify_failure_typed("edit file failed: conflict in unified diff") == FailureClass.PATCH_ERROR

    def test_tool_policy_blocked(self):
        assert classify_failure_typed("blocked by policy: requires approval") == FailureClass.TOOL_POLICY_BLOCKED

    def test_unknown(self):
        assert classify_failure_typed("") == FailureClass.UNKNOWN_ERROR


class TestSuggestedActions:
    def test_env_error_has_actions(self):
        actions = _suggest_actions_for(FailureClass.ENVIRONMENT_ERROR, "")
        assert len(actions) > 0
        assert any("依赖" in a.label for a in actions)

    def test_test_failure_has_actions(self):
        actions = _suggest_actions_for(FailureClass.TEST_FAILURE, "")
        assert any("修复" in a.label for a in actions)

    def test_unknown_has_retry(self):
        actions = _suggest_actions_for(FailureClass.UNKNOWN_ERROR, "")
        assert any("重试" in a.label for a in actions)


class TestAutoRetry:
    def test_model_error_can_auto_retry(self):
        assert _can_auto_retry(FailureClass.MODEL_ERROR)

    def test_command_error_can_auto_retry(self):
        assert _can_auto_retry(FailureClass.COMMAND_ERROR)

    def test_test_failure_cannot_auto_retry(self):
        assert not _can_auto_retry(FailureClass.TEST_FAILURE)

    def test_approval_rejected_cannot_auto_retry(self):
        assert not _can_auto_retry(FailureClass.APPROVAL_REJECTED)


class TestRunFailureClassification:
    def test_failed_run_with_error_events(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"fail_run_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="test failure",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="运行异常",
            content="ModuleNotFoundError: No module named 'requests'",
            agent="lead",
            workspace_dir=str(ws),
        )

        import src.infra.config as cfg
        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            records = classify_run_failures(thread_id)
            assert len(records) >= 1
            assert records[0].failure_class == FailureClass.ENVIRONMENT_ERROR
            assert len(records[0].evidence) > 0
        finally:
            cfg.WORKSPACE_DIR = old

    def test_failed_run_extracts_related_files(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        (ws / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (ws / "test_app.py").write_text("from app import add\n", encoding="utf-8")

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"related_files_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="fix tests",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="pytest failed",
            content="FAILED test_app.py::test_add - AssertionError",
            payload={"error": "test_app.py failed"},
            agent="tester",
            workspace_dir=str(ws),
        )

        records = classify_run_failures(thread_id, str(ws))
        failure = records[0]
        assert failure.failure_class == FailureClass.TEST_FAILURE
        assert failure.related_files == ["test_app.py"]
        assert failure.evidence["related_files"] == ["test_app.py"]

    def test_failed_run_no_errors_produces_unknown(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"unknown_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="no error events",
            workspace_dir=str(ws),
            status="failed",
        )

        import src.infra.config as cfg
        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            records = classify_run_failures(thread_id)
            assert len(records) >= 1
            assert records[0].failure_class == FailureClass.UNKNOWN_ERROR
        finally:
            cfg.WORKSPACE_DIR = old

    def test_approval_timeout_is_classified(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.approval_service import create_tool_approval
        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"approval_timeout_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="approval timeout",
            workspace_dir=str(ws),
            status="failed",
        )
        create_tool_approval(
            thread_id,
            {
                "decision_id": "approve_timeout",
                "tool": "run_command",
                "status": "pending",
                "requires_approval": True,
                "allowed": False,
                "reason": "需要审批",
                "risk_level": "high",
            },
            str(ws),
            timeout_seconds=-1,
        )

        records = classify_run_failures(thread_id, str(ws))
        assert any(r.failure_class == FailureClass.APPROVAL_TIMEOUT for r in records)

    def test_approval_rejection_is_classified(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.approval_service import create_tool_approval, resolve_tool_approval
        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"approval_rejected_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="approval rejected",
            workspace_dir=str(ws),
            status="failed",
        )
        create_tool_approval(
            thread_id,
            {
                "decision_id": "approve_reject",
                "tool": "delete_file",
                "status": "pending",
                "requires_approval": True,
                "allowed": False,
                "reason": "需要审批",
                "risk_level": "high",
            },
            str(ws),
        )
        resolve_tool_approval(thread_id, "approve_reject", approved=False, workspace_dir=str(ws))

        records = classify_run_failures(thread_id, str(ws))
        assert any(r.failure_class == FailureClass.APPROVAL_REJECTED for r in records)


class TestFailurePersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"persist_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="persist test",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="test error",
            content="AssertionError: test failed",
            agent="lead",
            workspace_dir=str(ws),
        )

        import src.infra.config as cfg
        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            records = save_failures(thread_id)
            assert len(records) >= 1

            loaded = load_failures(thread_id)
            assert len(loaded) == len(records)
            assert loaded[0].failure_id == records[0].failure_id
        finally:
            cfg.WORKSPACE_DIR = old

    def test_load_missing_returns_empty(self):
        assert load_failures("nonexistent_fail") == []

    def test_failures_json_exists(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"json_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="json test",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="test error",
            content="test failed",
            agent="lead",
            workspace_dir=str(ws),
        )

        import src.infra.config as cfg
        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            save_failures(thread_id)
            rd = ws / ".nanocursor" / "runs" / thread_id
            assert (rd / "failures.json").exists()
        finally:
            cfg.WORKSPACE_DIR = old


class TestRemediationPlanner:
    def test_plan_remediation_for_test_failure(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"rem_{uuid.uuid4().hex[:8]}"

        import src.infra.config as cfg

        store.create_session(
            thread_id=thread_id,
            prompt="test",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="fail",
            content="AssertionError: assert 1 == 2",
            agent="tester",
            workspace_dir=str(ws),
        )

        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            records = save_failures(thread_id)
            assert len(records) >= 1
            fid = records[0].failure_id

            plan = plan_remediation(fid, thread_id)
            assert plan is not None
            assert plan["original_thread_id"] == thread_id
            assert plan["strategy"] == "fix_test_failure"
            assert plan["auto_retry"] is False
        finally:
            cfg.WORKSPACE_DIR = old

    def test_plan_missing_failure_returns_none(self):
        assert plan_remediation("bad_id", "bad_run") is None

    def test_create_remediation_run(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"rem_run_{uuid.uuid4().hex[:8]}"

        import src.infra.config as cfg

        store.create_session(
            thread_id=thread_id,
            prompt="remediate test",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="fail",
            content="RateLimitError",
            agent="lead",
            workspace_dir=str(ws),
        )

        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            records = save_failures(thread_id)
            fid = records[0].failure_id

            result = create_remediation_run(thread_id, fid)
            assert result["created"] is True
            assert result["retry_thread_id"].startswith("remediation_")
            assert result["original_thread_id"] == thread_id
            assert result["strategy"] == "retry_with_backoff"
        finally:
            cfg.WORKSPACE_DIR = old


class TestFailureAPI:
    def test_get_failures_no_run(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"nofail_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/failures")
        assert resp.status_code == 404

    def test_get_failure_404(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"badf_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/failures/fake_id")
        assert resp.status_code == 404

    def test_post_remediate_404(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app, raise_server_exceptions=False)
        thread_id = f"badr_{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/runs/{thread_id}/failures/fake_id/remediate",
            json={"mode": "auto", "confirmed": True},
        )
        assert resp.status_code == 404

    def test_full_failure_flow(self, tmp_path):
        """End-to-end: create failed run, classify, query, remediate."""
        from fastapi.testclient import TestClient
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"e2efail_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="E2E failure",
            workspace_dir=str(ws),
            status="failed",
        )
        store.append_event(
            thread_id=thread_id,
            event_type="error",
            title="Test failed",
            content="AssertionError: assert False",
            agent="tester",
            workspace_dir=str(ws),
        )

        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)

            # Get failures (auto-classifies)
            resp = client.get(f"/api/runs/{thread_id}/failures")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] >= 1
            fid = data["failures"][0]["failure_id"]

            # Get single failure
            resp2 = client.get(f"/api/runs/{thread_id}/failures/{fid}")
            assert resp2.status_code == 200
            assert resp2.json()["failure_class"] == "test_failure"

            # Get remediation plan
            resp3 = client.post(
                f"/api/runs/{thread_id}/failures/{fid}/remediate",
                json={"mode": "auto", "confirmed": True},
            )
            assert resp3.status_code == 200
            assert resp3.json()["created"] is True
        finally:
            cfg.WORKSPACE_DIR = old
