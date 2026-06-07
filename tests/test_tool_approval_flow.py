"""Tool approval flow tests — persistence, resolve, API, and policy decisions."""

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.runtime.tool_policy_runtime import ToolPolicyRuntime, ToolPolicyDecision
from src.runtime.run_budget import RunBudget
from src.api.services.approval_service import (
    create_tool_approval,
    get_pending_approvals,
    get_tool_approval,
    resolve_tool_approval,
    wait_for_approval,
    wait_for_approval_async,
)


# ---------------------------------------------------------------------------
# ToolPolicyDecision unit tests
# ---------------------------------------------------------------------------

class TestToolPolicyDecision:
    def test_decision_defaults(self):
        d = ToolPolicyDecision(tool="bash")
        assert d.tool == "bash"
        assert d.allowed is True
        assert d.status == "auto_allowed"
        assert d.decision_id.startswith("approval_")

    def test_approval_decision_is_pending(self):
        d = ToolPolicyDecision(tool="write_file", requires_approval=True)
        assert d.status == "pending"

    def test_blocked_decision(self):
        d = ToolPolicyDecision(tool="rm", allowed=False, status="blocked",
                               reason="禁止删除")
        assert not d.allowed
        assert d.status == "blocked"

    def test_to_dict_includes_all_fields(self):
        d = ToolPolicyDecision(tool="bash", requires_approval=True, risk_level="high")
        dd = d.to_dict()
        assert dd["tool"] == "bash"
        assert dd["status"] == "pending"
        assert dd["risk_level"] == "high"
        assert "decision_id" in dd
        assert "created_at" in dd


# ---------------------------------------------------------------------------
# ToolPolicyRuntime tests
# ---------------------------------------------------------------------------

class TestPolicyRuntime:
    def test_denied_tool_blocked(self):
        rt = ToolPolicyRuntime(policy={"denied_tools": ["bash"]})
        d = rt.check("bash")
        assert d.allowed is False
        assert d.status == "blocked"

    def test_not_in_allowed_list_blocked(self):
        rt = ToolPolicyRuntime(policy={"allowed_tools": ["read_file"]})
        d = rt.check("write_file")
        assert d.allowed is False

    def test_budget_exceeded_blocks(self):
        rt = ToolPolicyRuntime(policy={}, budget=RunBudget(max_tool_calls=0))
        d = rt.check("bash")
        assert d.allowed is False
        assert "max_tool_calls" in d.budget_exceeded

    def test_approval_required_is_pending(self):
        rt = ToolPolicyRuntime(policy={"approval_required": ["write_file"]})
        d = rt.check("write_file")
        assert d.requires_approval is True
        assert d.status == "pending"
        assert d.allowed is True  # allowed but needs approval

    def test_normal_tool_auto_allowed(self):
        rt = ToolPolicyRuntime()
        d = rt.check("read_file")
        assert d.allowed is True
        assert d.status == "auto_allowed"
        assert d.requires_approval is False


# ---------------------------------------------------------------------------
# Approval service tests
# ---------------------------------------------------------------------------

class TestApprovalService:
    def test_create_and_list_pending(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="write_file", requires_approval=True,
                               risk_level="high")
        create_tool_approval("run1", d, str(ws))
        pending = get_pending_approvals("run1", str(ws))
        assert len(pending) == 1
        assert pending[0]["tool"] == "write_file"
        assert pending[0]["status"] == "pending"
        assert pending[0]["thread_id"] == "run1"
        assert pending[0]["expires_at"] > pending[0]["created_at"]

    def test_get_tool_approval_returns_single_record(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run_lookup", d, str(ws))

        record = get_tool_approval("run_lookup", d.decision_id, str(ws))

        assert record is not None
        assert record["decision_id"] == d.decision_id
        assert record["status"] == "pending"

    def test_resolve_approval_approved(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run2", d, str(ws))
        resolved = resolve_tool_approval("run2", d.decision_id, True, "允许", str(ws))
        assert resolved["status"] == "approved"

    def test_resolve_approval_rejected(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run3", d, str(ws))
        resolved = resolve_tool_approval("run3", d.decision_id, False, "", str(ws))
        assert resolved["status"] == "rejected"

    def test_resolve_nonexistent_returns_none(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        result = resolve_tool_approval("runX", "nonexistent", True, "", str(ws))
        assert result is None

    def test_resolved_no_longer_pending(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run4", d, str(ws))
        resolve_tool_approval("run4", d.decision_id, True, "", str(ws))
        pending = get_pending_approvals("run4", str(ws))
        assert len(pending) == 0

    def test_expired_approval_is_rejected_on_read(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run_expired", d, str(ws), timeout_seconds=-1)

        pending = get_pending_approvals("run_expired", str(ws))
        record = get_tool_approval("run_expired", d.decision_id, str(ws))

        assert pending == []
        assert record is not None
        assert record["status"] == "rejected"
        assert "超时" in record["reason"]

    def test_resolve_expired_approval_does_not_approve(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run_late", d, str(ws), timeout_seconds=-1)

        resolved = resolve_tool_approval("run_late", d.decision_id, True, "late", str(ws))

        assert resolved is not None
        assert resolved["status"] == "rejected"
        assert "超时" in resolved["reason"]

    def test_wait_for_approval_times_out(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        d = ToolPolicyDecision(tool="bash", requires_approval=True)
        create_tool_approval("run5", d, str(ws))
        # Use short timeout for test
        resolved = wait_for_approval("run5", d, timeout_seconds=0.1, workspace_dir=str(ws))
        assert resolved["status"] == "rejected"
        assert "超时" in resolved.get("reason", "") or "timeout" in resolved.get("reason", "")

    def test_wait_for_approval_async_resolves_without_blocking_loop(self, tmp_path):
        async def scenario():
            ws = tmp_path / "ws"
            ws.mkdir()
            d = ToolPolicyDecision(tool="bash", requires_approval=True)
            create_tool_approval("run_async", d, str(ws))

            async def resolver():
                await asyncio.sleep(0.02)
                resolve_tool_approval("run_async", d.decision_id, True, "允许", str(ws))

            task = asyncio.create_task(resolver())
            resolved = await wait_for_approval_async(
                "run_async",
                d,
                timeout_seconds=1,
                workspace_dir=str(ws),
                poll_interval_seconds=0.01,
            )
            await task
            return resolved

        resolved = asyncio.run(scenario())
        assert resolved["status"] == "approved"
        assert resolved["comment"] == "允许"

    def test_wait_for_approval_async_abort_auto_rejects(self, tmp_path):
        async def scenario():
            ws = tmp_path / "ws"
            ws.mkdir()
            d = ToolPolicyDecision(tool="bash", requires_approval=True)
            create_tool_approval("run_abort", d, str(ws))
            return await wait_for_approval_async(
                "run_abort",
                d,
                timeout_seconds=1,
                workspace_dir=str(ws),
                poll_interval_seconds=0.01,
                should_abort=lambda: True,
            )

        resolved = asyncio.run(scenario())
        assert resolved["status"] == "rejected"
        assert "取消" in resolved["reason"] or "中断" in resolved["reason"]


# ---------------------------------------------------------------------------
# API-level approval tests
# ---------------------------------------------------------------------------

class TestApprovalAPI:
    def test_list_approvals_empty(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.get("/api/runs/no_such_run/approvals")
        # May be 200 with empty list or 404
        assert resp.status_code in (200, 404)

    def test_resolve_approval_not_found(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.post(
            "/api/runs/fake_run/approvals/fake_id",
            json={"approved": True},
        )
        assert resp.status_code in (404, 400)

    def test_get_approval_not_found(self):
        from src.api.server import app
        client = TestClient(app)
        resp = client.get("/api/runs/fake_run/approvals/fake_id")
        assert resp.status_code == 404
