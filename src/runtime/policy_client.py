"""gRPC client for the Go policy engine service."""

import os
from typing import Optional

import grpc

from src.indexer.proto import policy_pb2, policy_pb2_grpc


class PolicyClient:
    """gRPC client for the Go policy engine."""

    def __init__(self, server_addr: Optional[str] = None):
        if server_addr is None:
            server_addr = os.environ.get("POLICY_GRPC_ADDR", "localhost:50052")
        self._addr = server_addr
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[policy_pb2_grpc.PolicyStub] = None

    def _ensure_channel(self):
        if self._channel is None:
            self._channel = grpc.insecure_channel(self._addr)
            self._stub = policy_pb2_grpc.PolicyStub(self._channel)

    def check_tool(self, tool_name: str, tool_input: str = "", run_id: str = "") -> dict:
        self._ensure_channel()
        resp = self._stub.CheckTool(policy_pb2.CheckToolRequest(
            tool_name=tool_name, tool_input=tool_input, run_id=run_id,
        ))
        return {
            "decision": resp.decision,
            "reason": resp.reason,
            "risk_level": resp.risk_level,
        }

    def check_action(self, command: str, run_id: str = "") -> dict:
        self._ensure_channel()
        resp = self._stub.CheckAction(policy_pb2.CheckActionRequest(
            command=command, run_id=run_id,
        ))
        return {
            "decision": resp.decision,
            "reason": resp.reason,
            "risk_level": resp.risk_level,
            "command_type": resp.command_type,
        }

    def record_result(self, tool_name: str, success: bool, run_id: str = "", error_message: str = "") -> dict:
        self._ensure_channel()
        resp = self._stub.RecordResult(policy_pb2.RecordResultRequest(
            tool_name=tool_name, success=success, run_id=run_id, error_message=error_message,
        ))
        return {
            "policy_changed": resp.policy_changed,
            "new_decision": resp.new_decision,
            "adaptation_reason": resp.adaptation_reason,
        }

    def close(self):
        if self._channel:
            self._channel.close()
            self._channel = None
            self._stub = None
