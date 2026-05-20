"""Action policy and audit log tests."""

import uuid
from pathlib import Path

from src.runtime.action_policy import (
    ActionDecision,
    ActionKind,
    ActionRequest,
    _classify_action_risk,
    check_action,
)
from src.runtime.audit_log import AuditLogRepository, AuditRecord, get_audit_repo
from src.api.services.action_execution_service import (
    check_and_decide,
    execute_action,
    get_audit_trail,
    record_action_result,
)


class TestActionKind:
    def test_all_kinds(self):
        values = {k.value for k in ActionKind}
        assert "read_file" in values
        assert "write_file" in values
        assert "delete_file" in values
        assert "run_command" in values
        assert "git_operation" in values
        assert "mcp_call" in values
        assert "recovery_action" in values


class TestActionRequest:
    def test_auto_generates_id_and_risk(self):
        req = ActionRequest(thread_id="run_1", kind=ActionKind.RUN_COMMAND, target="rm file")
        assert req.action_id.startswith("act_")
        assert req.risk == "high"


class TestActionDecision:
    def test_high_risk_requires_approval(self):
        d = check_action(ActionKind.DELETE_FILE, "src/file.py")
        assert d.requires_approval is True
        assert d.risk == "high"

    def test_low_risk_auto_allowed(self):
        d = check_action(ActionKind.READ_FILE, "README.md")
        assert d.requires_approval is False
        assert d.risk == "low"

    def test_medium_risk_allowed_no_approval(self):
        d = check_action(ActionKind.WRITE_FILE, "src/utils.py")
        assert d.allowed is True
        assert d.requires_approval is False


class TestRiskClassification:
    def test_delete_high(self):
        assert _classify_action_risk(ActionKind.DELETE_FILE) == "high"

    def test_run_command_high(self):
        assert _classify_action_risk(ActionKind.RUN_COMMAND) == "high"

    def test_mcp_call_high(self):
        assert _classify_action_risk(ActionKind.MCP_CALL) == "high"

    def test_recovery_high(self):
        assert _classify_action_risk(ActionKind.RECOVERY_ACTION) == "high"

    def test_read_low(self):
        assert _classify_action_risk(ActionKind.READ_FILE) == "low"

    def test_git_discard_high(self):
        assert _classify_action_risk(ActionKind.GIT_OPERATION, "git reset --hard") == "high"

    def test_git_commit_medium(self):
        assert _classify_action_risk(ActionKind.GIT_OPERATION, "git commit") == "medium"

    def test_write_env_high(self):
        assert _classify_action_risk(ActionKind.WRITE_FILE, ".env") == "high"

    def test_write_secret_high(self):
        assert _classify_action_risk(ActionKind.WRITE_FILE, "src/secrets.py") == "high"

    def test_write_lockfile_high(self):
        assert _classify_action_risk(ActionKind.WRITE_FILE, "package-lock.json") == "high"

    def test_write_normal_medium(self):
        assert _classify_action_risk(ActionKind.WRITE_FILE, "src/app.py") == "medium"


class TestAuditLog:
    def test_append_and_list(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        repo = AuditLogRepository()
        repo.append(AuditRecord(
            audit_id="a1", thread_id="run_1", kind="write_file",
            target="a.py", decision="auto_allowed", result="success",
            reason="ok", created_at=1000.0,
        ), str(ws))
        repo.append(AuditRecord(
            audit_id="a2", thread_id="run_1", kind="run_command",
            target="npm test", decision="approved", result="success",
            reason="approved by user", created_at=1001.0,
        ), str(ws))
        records = repo.list("run_1", str(ws))
        assert len(records) == 2
        assert records[0].kind == "write_file"
        assert records[1].target == "npm test"

    def test_list_empty(self):
        repo = AuditLogRepository()
        assert repo.list("nobody") == []

    def test_count(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        repo = AuditLogRepository()
        for i in range(5):
            repo.append(AuditRecord(
                audit_id=f"a{i}", thread_id="run_c", kind="write_file",
                target=f"f{i}.py", decision="auto_allowed", result="success",
                reason="ok", created_at=float(i),
            ), str(ws))
        assert repo.count("run_c", str(ws)) == 5

    def test_count_empty(self):
        repo = AuditLogRepository()
        assert repo.count("nobody") == 0


class TestActionExecutionService:
    def test_check_invalid_kind(self):
        result = check_and_decide(kind="invalid_kind", target="x")
        assert result["allowed"] is False

    def test_check_high_risk_needs_approval(self):
        result = check_and_decide(kind="delete_file", target="src/old.py")
        assert result["requires_approval"] is True

    def test_check_read_file_low_risk(self):
        result = check_and_decide(kind="read_file", target="README.md")
        assert result["allowed"] is True
        assert result["requires_approval"] is False

    def test_execute_invalid_kind(self):
        result = execute_action(kind="bad", target="x", thread_id="t1")
        assert result["allowed"] is False

    def test_execute_auto_allowed(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        (ws / "f.txt").write_text("ok")
        result = execute_action(
            kind="read_file", target="f.txt",
            thread_id="run_ea", workspace_dir=str(ws),
        )
        assert result["allowed"] is True
        assert result["requires_approval"] is False

    def test_execute_writes_audit_to_supplied_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        (ws / "f.txt").write_text("ok")
        execute_action(
            kind="read_file", target="f.txt",
            thread_id="run_workspace_audit", workspace_dir=str(ws),
        )
        trail = get_audit_trail("run_workspace_audit", str(ws))
        assert trail["total"] == 1

    def test_path_escape_is_denied(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        result = check_and_decide(
            kind="write_file", target="../../etc/passwd",
            thread_id="run_escape", workspace_dir=str(ws),
        )
        assert result["allowed"] is False
        assert result["risk"] == "high"

    def test_high_risk_execute_creates_pending_approval(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        result = execute_action(
            kind="run_command", target="pytest -q",
            thread_id="run_needs_approval", workspace_dir=str(ws),
        )
        assert result["allowed"] is True
        assert result["requires_approval"] is True
        assert result["approval_id"]
        from src.api.services.approval_service import get_tool_approval
        approval = get_tool_approval("run_needs_approval", result["approval_id"], str(ws))
        assert approval is not None
        assert approval["status"] == "pending"

    def test_record_action_result(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        record_action_result(
            thread_id="run_rec", action_id="act_test", result="success",
            detail={"output": "ok"}, duration_ms=100, workspace_dir=str(ws),
        )
        trail = get_audit_trail("run_rec", str(ws))
        assert len(trail["records"]) >= 1

    def test_audit_trail_full(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        repo = AuditLogRepository()
        repo.append(AuditRecord(
            audit_id="aa1", thread_id="run_at", kind="write_file",
            target="x.py", decision="auto_allowed", result="success",
            reason="ok", created_at=1.0,
        ), str(ws))
        trail = get_audit_trail("run_at", str(ws))
        assert trail["total"] == 1
        assert trail["records"][0]["kind"] == "write_file"


class TestActionPolicyAPI:
    def test_check_endpoint(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app)
        thread_id = f"run_ap_{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/runs/{thread_id}/actions/check",
            json={"kind": "delete_file", "target": "old.py"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["requires_approval"] is True

    def test_execute_endpoint(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app)
        thread_id = f"run_ae_{uuid.uuid4().hex[:8]}"
        resp = client.post(
            f"/api/runs/{thread_id}/actions/execute",
            json={"kind": "read_file", "target": "readme.md"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True

    def test_audit_endpoint(self):
        from fastapi.testclient import TestClient
        from api_server import app
        client = TestClient(app)
        thread_id = f"run_aud_{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/runs/{thread_id}/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "records" in data
        assert "total" in data

    def test_execute_and_check_audit(self, tmp_path):
        """Execute an action, then verify it appears in audit trail."""
        from fastapi.testclient import TestClient
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)

        from src.api.services.event_store import EventStore
        store = EventStore()
        thread_id = f"run_e2ea_{uuid.uuid4().hex[:8]}"
        store.create_session(
            thread_id=thread_id,
            prompt="audit e2e",
            workspace_dir=str(ws),
            status="running",
        )

        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={"kind": "write_file", "target": "src/main.py"},
            )
            assert resp.status_code == 200

            resp2 = client.get(f"/api/runs/{thread_id}/audit")
            assert resp2.status_code == 200
            assert resp2.json()["total"] >= 1
        finally:
            cfg.WORKSPACE_DIR = old

    def test_check_endpoint_denies_path_escape(self, tmp_path):
        from fastapi.testclient import TestClient
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app)
            resp = client.post(
                "/api/runs/run_escape_api/actions/check",
                json={"kind": "write_file", "target": "../../etc/passwd"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["allowed"] is False
            assert data["risk"] == "high"
        finally:
            cfg.WORKSPACE_DIR = old

    def test_execute_endpoint_high_risk_returns_pending_not_500(self, tmp_path):
        from fastapi.testclient import TestClient
        from api_server import app
        import src.infra.config as cfg

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        old = cfg.WORKSPACE_DIR
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/runs/run_high_risk_api/actions/execute",
                json={"kind": "run_command", "target": "pytest -q"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["requires_approval"] is True
            assert data["approval_id"]
        finally:
            cfg.WORKSPACE_DIR = old
