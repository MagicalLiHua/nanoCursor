"""gRPC client for go-mcp service."""

import json
import os

import grpc

from src.mcp.proto import mcp_pb2 as pb
from src.mcp.proto import mcp_pb2_grpc as pb_grpc

MCP_ADDR = os.getenv("NANOCURSOR_MCP_ADDR", "localhost:50056")

_channel = None
_stub = None


def _ensure_channel():
    global _channel, _stub
    if _channel is None:
        _channel = grpc.insecure_channel(MCP_ADDR)
        _stub = pb_grpc.MCPServiceStub(_channel)
    return _stub


def close():
    global _channel, _stub
    if _channel is not None:
        _channel.close()
        _channel = None
        _stub = None


def health():
    stub = _ensure_channel()
    resp = stub.Health(pb.HealthRequest(), timeout=5)
    return {"ok": resp.ok, "service": resp.service, "version": resp.version}


def list_presets():
    stub = _ensure_channel()
    resp = stub.ListPresets(pb.ListPresetsRequest(), timeout=5)
    return [{"id": p.id, "name": p.name, "description": p.description,
             "command": p.command, "args": list(p.args)} for p in resp.presets]


def list_servers():
    stub = _ensure_channel()
    resp = stub.ListServers(pb.ListServersRequest(), timeout=5)
    return [{"id": s.id, "name": s.name, "command": s.command,
             "args": list(s.args)} for s in resp.servers]


def probe_server(server_id, command, args=None, env=None, env_keys=None, workspace_dir=""):
    stub = _ensure_channel()
    resp = stub.ProbeServer(pb.ProbeRequest(
        server_id=server_id,
        workspace_dir=workspace_dir,
        command=command,
        args=args or [],
        env=env or {},
        env_keys=env_keys or [],
    ), timeout=10)
    return {
        "server_id": resp.server_id,
        "status": resp.status,
        "ok": resp.ok,
        "error": resp.error,
    }


def list_mcp_tools(server_id):
    stub = _ensure_channel()
    resp = stub.ListServerTools(pb.ListToolsRequest(server_id=server_id), timeout=15)
    tools = [{"name": t.name, "description": t.description,
              "permission_level": t.permission_level,
              "requires_approval": t.requires_approval} for t in resp.tools]
    return {"tools": tools, "status": resp.status, "ok": resp.ok, "error": resp.error}


def call_mcp_tool(server_id, tool_name, arguments=None, workspace_dir="",
                   permission_level="", requires_approval=False,
                   approval_id="", approval_token="", run_id=""):
    stub = _ensure_channel()
    arguments_json = json.dumps(arguments or {})
    resp = stub.CallTool(pb.CallToolRequest(
        server_id=server_id,
        tool_name=tool_name,
        arguments=arguments_json,
        workspace_dir=workspace_dir,
        permission_level=permission_level,
        requires_approval=requires_approval,
        approval_id=approval_id,
        approval_token=approval_token,
        run_id=run_id,
    ), timeout=15)
    return {
        "server_id": resp.server_id,
        "tool": resp.tool,
        "ok": resp.ok,
        "result": resp.result,
        "error": resp.error,
        "error_code": resp.error_code,
        "permission_level": resp.permission_level,
        "requires_approval": resp.requires_approval,
    }
