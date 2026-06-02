"""Action policy and audit log tests."""

import json
import sys
import uuid
from pathlib import Path

from src.runtime.action_policy import (
    ActionDecision,
    ActionKind,
    ActionRequest,
    _classify_action_risk,
    check_action,
    classify_action_permission,
)
from src.runtime.audit_log import AuditLogRepository, AuditRecord, get_audit_repo
from src.api.services.action_execution_service import (
    check_and_decide,
    execute_action,
    get_audit_trail,
    record_action_result,
)


def write_fake_mcp_server(workspace: Path) -> Path:
    script = workspace / "fake_mcp_server.py"
    script.write_text(
        r'''
import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii").partition(":")
        headers[key.strip().lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers["content-length"])).decode("utf-8"))


def write_message(message):
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/call":
        arguments = message.get("params", {}).get("arguments", {})
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "echo:" + str(arguments.get("text", ""))}]}})
    else:
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "echo"}]}})
''',
        encoding="utf-8",
    )
    return script


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

    def test_run_command_shell_safe_medium(self):
        assert _classify_action_risk(ActionKind.RUN_COMMAND, "pytest -q") == "medium"
        d = check_action(ActionKind.RUN_COMMAND, "pytest -q")
        assert d.requires_approval is False
        assert d.permission_level == "shell_safe"

    def test_mcp_call_high(self):
        assert _classify_action_risk(ActionKind.MCP_CALL) == "high"

    def test_mcp_read_tool_is_low_risk(self):
        assert classify_action_permission(
            ActionKind.MCP_CALL,
            "mcp.github/list_issues",
            payload={"tool_name": "list_issues"},
        ) == "mcp_read"
        d = check_action(
            ActionKind.MCP_CALL,
            "mcp.github/list_issues",
            payload={"tool_name": "list_issues"},
        )
        assert d.risk == "low"
        assert d.requires_approval is False
        assert d.permission_level == "mcp_read"

    def test_mcp_write_tool_requires_approval(self):
        d = check_action(
            ActionKind.MCP_CALL,
            "mcp.github/create_pr",
            payload={"tool_name": "create_pr"},
        )
        assert d.risk == "high"
        assert d.requires_approval is True
        assert d.permission_level == "mcp_write"

    def test_recovery_high(self):
        assert _classify_action_risk(ActionKind.RECOVERY_ACTION) == "high"

    def test_read_low(self):
        assert _classify_action_risk(ActionKind.READ_FILE) == "low"

    def test_git_discard_high(self):
        assert _classify_action_risk(ActionKind.GIT_OPERATION, "git reset --hard") == "high"

    def test_git_commit_high(self):
        assert _classify_action_risk(ActionKind.GIT_OPERATION, "git commit") == "high"

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
        assert result["result"] == "success"
        assert result["detail"]["content"] == "ok"

    def test_execute_read_missing_file_records_failure(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        result = execute_action(
            kind="read_file", target="missing.txt",
            thread_id="run_read_missing", workspace_dir=str(ws),
        )
        assert result["allowed"] is True
        assert result["result"] == "failure"
        trail = get_audit_trail("run_read_missing", str(ws))
        assert trail["records"][0]["result"] == "failure"

    def test_execute_write_file_writes_content_and_checkpoint(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        target = ws / "src" / "main.py"
        target.parent.mkdir()
        target.write_text("old", encoding="utf-8")
        result = execute_action(
            kind="write_file", target="src/main.py",
            payload={"content": "new"},
            thread_id="run_write_real", workspace_dir=str(ws),
        )
        assert result["allowed"] is True
        assert result["result"] == "success"
        assert target.read_text(encoding="utf-8") == "new"
        assert result["detail"]["checkpoint"]["filepath"] == "src/main.py"
        checkpoints = list((ws / ".checkpoints" / "run_write_real").glob("*.meta.json"))
        assert checkpoints

    def test_execute_write_new_file_has_no_checkpoint(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        result = execute_action(
            kind="write_file", target="src/new.py",
            payload={"content": "print('hi')"},
            thread_id="run_write_new", workspace_dir=str(ws),
        )
        assert result["result"] == "success"
        assert (ws / "src" / "new.py").read_text(encoding="utf-8") == "print('hi')"
        assert result["detail"]["checkpoint"] is None

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

    def test_safe_run_command_executes_without_approval(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        result = execute_action(
            kind="run_command", target="echo hello",
            thread_id="run_cmd_safe", workspace_dir=str(ws),
        )
        assert result["allowed"] is True
        assert result["requires_approval"] is False
        assert result["risk"] == "medium"
        assert result["permission_level"] == "shell_safe"
        assert result["result"] == "success"
        assert "hello" in result["detail"]["stdout"]

    def test_high_risk_execute_creates_pending_approval(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        result = execute_action(
            kind="run_command", target="rm -rf dist",
            thread_id="run_needs_approval", workspace_dir=str(ws),
        )
        assert result["allowed"] is True
        assert result["requires_approval"] is True
        assert result["approval_id"]
        from src.api.services.approval_service import get_tool_approval
        approval = get_tool_approval("run_needs_approval", result["approval_id"], str(ws))
        assert approval is not None
        assert approval["status"] == "pending"
        assert approval["kind"] == "run_command"
        assert approval["target"] == "rm -rf dist"

    def test_approved_run_command_executes_and_records_output(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        pending = execute_action(
            kind="run_command", target="rm -rf dist",
            thread_id="run_cmd_approved", workspace_dir=str(ws),
        )
        from src.api.services.approval_service import resolve_tool_approval
        resolve_tool_approval(
            "run_cmd_approved",
            pending["approval_id"],
            approved=True,
            comment="允许",
            workspace_dir=str(ws),
        )

        result = execute_action(
            kind="run_command", target="rm -rf dist",
            payload={"approval_id": pending["approval_id"]},
            thread_id="run_cmd_approved", workspace_dir=str(ws),
        )

        assert result["requires_approval"] is False
        assert result["result"] == "success"
        assert result["detail"]["exit_code"] == 0
        trail = get_audit_trail("run_cmd_approved", str(ws))
        assert trail["records"][-1]["decision"] == "approved"

    def test_rejected_run_command_does_not_execute(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        pending = execute_action(
            kind="run_command", target="rm -rf blocked",
            thread_id="run_cmd_rejected", workspace_dir=str(ws),
        )
        from src.api.services.approval_service import resolve_tool_approval
        resolve_tool_approval(
            "run_cmd_rejected",
            pending["approval_id"],
            approved=False,
            comment="不允许",
            workspace_dir=str(ws),
        )

        result = execute_action(
            kind="run_command", target="rm -rf blocked",
            payload={"approval_id": pending["approval_id"]},
            thread_id="run_cmd_rejected", workspace_dir=str(ws),
        )

        assert result["allowed"] is False
        assert result["result"] == "failure"
        assert "approved" in result["reason"]

    def test_approved_run_command_target_mismatch_is_denied(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        pending = execute_action(
            kind="run_command", target="rm -rf one",
            thread_id="run_cmd_mismatch", workspace_dir=str(ws),
        )
        from src.api.services.approval_service import resolve_tool_approval
        resolve_tool_approval(
            "run_cmd_mismatch",
            pending["approval_id"],
            approved=True,
            workspace_dir=str(ws),
        )

        result = execute_action(
            kind="run_command", target="rm -rf two",
            payload={"approval_id": pending["approval_id"]},
            thread_id="run_cmd_mismatch", workspace_dir=str(ws),
        )

        assert result["allowed"] is False
        assert "不匹配" in result["reason"]

    def test_delete_file_without_approval_returns_pending(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        target = ws / "src" / "old.py"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")

        result = execute_action(
            kind="delete_file", target="src/old.py",
            thread_id="run_delete_pending", workspace_dir=str(ws),
        )

        assert result["result"] == "pending"
        assert result["requires_approval"] is True
        assert target.exists()

    def test_approved_delete_file_moves_to_trash_and_checkpoint(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        target = ws / "src" / "old.py"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")

        pending = execute_action(
            kind="delete_file", target="src/old.py",
            thread_id="run_delete_approved", workspace_dir=str(ws),
        )
        from src.api.services.approval_service import resolve_tool_approval
        resolve_tool_approval(
            "run_delete_approved",
            pending["approval_id"],
            approved=True,
            comment="允许删除",
            workspace_dir=str(ws),
        )

        result = execute_action(
            kind="delete_file", target="src/old.py",
            payload={"approval_id": pending["approval_id"]},
            thread_id="run_delete_approved", workspace_dir=str(ws),
        )

        assert result["result"] == "success"
        assert not target.exists()
        assert result["detail"]["checkpoint"]["filepath"] == "src/old.py"
        trash_path = Path(result["detail"]["trash_path"])
        assert trash_path.exists()
        assert trash_path.read_text(encoding="utf-8") == "old"
        trail = get_audit_trail("run_delete_approved", str(ws))
        assert trail["records"][-1]["kind"] == "delete_file"
        assert trail["records"][-1]["result"] == "success"

    def test_approved_delete_file_rejects_directories(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        (ws / "src").mkdir()
        pending = execute_action(
            kind="delete_file", target="src",
            thread_id="run_delete_dir", workspace_dir=str(ws),
        )
        from src.api.services.approval_service import resolve_tool_approval
        resolve_tool_approval(
            "run_delete_dir",
            pending["approval_id"],
            approved=True,
            workspace_dir=str(ws),
        )

        result = execute_action(
            kind="delete_file", target="src",
            payload={"approval_id": pending["approval_id"]},
            thread_id="run_delete_dir", workspace_dir=str(ws),
        )

        assert result["result"] == "failure"
        assert "普通文件" in result["reason"]
        assert (ws / "src").is_dir()

    def test_approved_mcp_call_executes_stdio_tool(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        script = write_fake_mcp_server(ws)
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        (nanodir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"fake": {"command": sys.executable, "args": [str(script)]}}}),
            encoding="utf-8",
        )

        pending = execute_action(
            kind="mcp_call",
            target="mcp.fake/echo",
            payload={"server_id": "mcp.fake", "tool_name": "echo", "arguments": {"text": "hello"}},
            thread_id="run_mcp_approved",
            workspace_dir=str(ws),
        )
        from src.api.services.approval_service import resolve_tool_approval
        resolve_tool_approval(
            "run_mcp_approved",
            pending["approval_id"],
            approved=True,
            workspace_dir=str(ws),
        )

        result = execute_action(
            kind="mcp_call",
            target="mcp.fake/echo",
            payload={
                "approval_id": pending["approval_id"],
                "server_id": "mcp.fake",
                "tool_name": "echo",
                "arguments": {"text": "hello"},
            },
            thread_id="run_mcp_approved",
            workspace_dir=str(ws),
        )

        assert result["result"] == "success"
        assert result["detail"]["ok"] is True
        assert result["detail"]["result"]["content"][0]["text"] == "echo:hello"
        trail = get_audit_trail("run_mcp_approved", str(ws))
        assert trail["records"][-1]["kind"] == "mcp_call"

    def test_readonly_mcp_call_executes_without_approval(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        script = write_fake_mcp_server(ws)
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        (nanodir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"fake": {"command": sys.executable, "args": [str(script)]}}}),
            encoding="utf-8",
        )

        result = execute_action(
            kind="mcp_call",
            target="mcp.fake/list_echo",
            payload={"server_id": "mcp.fake", "tool_name": "list_echo", "arguments": {"text": "hello"}},
            thread_id="run_mcp_read",
            workspace_dir=str(ws),
        )

        assert result["allowed"] is True
        assert result["requires_approval"] is False
        assert result["permission_level"] == "mcp_read"
        assert result["result"] == "success"
        assert result["detail"]["ok"] is True
        assert result["detail"]["result"]["content"][0]["text"] == "echo:hello"

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
                json={"kind": "write_file", "target": "src/main.py", "payload": {"content": "hello"}},
            )
            assert resp.status_code == 200
            assert (ws / "src" / "main.py").read_text(encoding="utf-8") == "hello"

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
                json={"kind": "run_command", "target": "rm -rf dist"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["requires_approval"] is True
            assert data["approval_id"]
        finally:
            cfg.WORKSPACE_DIR = old

    def test_execute_endpoint_runs_command_after_approval(self, tmp_path):
        from fastapi.testclient import TestClient
        from api_server import app
        import src.infra.config as cfg
        from src.api.services.approval_service import resolve_tool_approval

        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        old = cfg.WORKSPACE_DIR
        thread_id = "run_high_risk_api_approved"
        try:
            cfg.WORKSPACE_DIR = str(ws)
            client = TestClient(app, raise_server_exceptions=False)
            pending_resp = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={"kind": "run_command", "target": "rm -rf dist"},
            )
            approval_id = pending_resp.json()["approval_id"]
            resolve_tool_approval(thread_id, approval_id, True, "允许", str(ws))

            exec_resp = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={"kind": "run_command", "target": "rm -rf dist", "approval_id": approval_id},
            )

            assert exec_resp.status_code == 200
            data = exec_resp.json()
            assert data["result"] == "success"
        finally:
            cfg.WORKSPACE_DIR = old
