"""Tests for the Go policy engine gRPC client."""

from unittest.mock import MagicMock, patch

import pytest


class TestPolicyClient:
    """Unit tests with mocked gRPC stub."""

    @patch("src.runtime.policy_client.grpc.insecure_channel")
    def _make_client(self, mock_channel):
        from src.runtime.policy_client import PolicyClient

        mock_stub = MagicMock()
        mock_channel.return_value = MagicMock()

        with patch("src.runtime.policy_client.policy_pb2_grpc.PolicyStub", return_value=mock_stub):
            client = PolicyClient(server_addr="localhost:50052")
            client._stub = mock_stub
            client._channel = MagicMock()
            return client, mock_stub

    def test_check_tool_read(self):
        client, stub = self._make_client()
        stub.CheckTool.return_value = MagicMock(
            decision="allow", reason="只读工具", risk_level="low"
        )
        result = client.check_tool("read_file")
        assert result["decision"] == "allow"
        assert result["risk_level"] == "low"

    def test_check_tool_deny(self):
        client, stub = self._make_client()
        stub.CheckTool.return_value = MagicMock(
            decision="require_approval", reason="高风险写操作", risk_level="high"
        )
        result = client.check_tool("delete_file")
        assert result["decision"] == "require_approval"

    def test_check_action_safe(self):
        client, stub = self._make_client()
        stub.CheckAction.return_value = MagicMock(
            decision="allow", reason="安全命令", risk_level="low", command_type="shell_safe"
        )
        result = client.check_action("git status")
        assert result["decision"] == "allow"
        assert result["command_type"] == "shell_safe"

    def test_check_action_risky(self):
        client, stub = self._make_client()
        stub.CheckAction.return_value = MagicMock(
            decision="require_approval", reason="高风险命令", risk_level="high", command_type="shell_risky"
        )
        result = client.check_action("rm -rf /")
        assert result["decision"] == "require_approval"

    def test_record_result(self):
        client, stub = self._make_client()
        stub.RecordResult.return_value = MagicMock(
            policy_changed=True, new_decision="high", adaptation_reason="连续 3 次失败"
        )
        result = client.record_result("bash", success=False, run_id="test")
        assert result["policy_changed"] is True
        assert "失败" in result["adaptation_reason"]

    def test_close(self):
        client, _ = self._make_client()
        mock_channel = MagicMock()
        client._channel = mock_channel
        client.close()
        mock_channel.close.assert_called_once()
        assert client._channel is None
