#!/usr/bin/env python3
"""Run a small end-to-end API smoke suite against the in-process FastAPI app.

This script is intentionally not a replacement for pytest.  It is a fast
operator check for catching route regressions such as 500 responses, wrong
argument order, broken workspace isolation, and missing confirmation guards.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class SmokeResult:
    method: str
    path: str
    status_code: int
    ok: bool
    detail: str = ""


class SmokeFailure(Exception):
    """Raised when one or more smoke checks fail."""


def _response_body(response: Any) -> str:
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception:
        return response.text[:500]


def _request(
    client: Any,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200,),
) -> SmokeResult:
    request = getattr(client, method.lower())
    response = request(path, json=body) if body is not None else request(path)
    ok = response.status_code in expected
    detail = "" if ok else _response_body(response)
    return SmokeResult(method, path, response.status_code, ok, detail)


def _assert_json_has(response: Any, *keys: str) -> None:
    data = response.json()
    missing = [key for key in keys if key not in data]
    if missing:
        raise SmokeFailure(f"Response is missing keys {missing}: {data}")


def _prepare_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("hello from api smoke\n", encoding="utf-8")
    (workspace / "fake_mcp_server.py").write_text(
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
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


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
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "smoke", "version": "1"}}})
    elif method == "tools/list":
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "smoke_echo", "description": "Smoke echo", "inputSchema": {"type": "object"}}]}})
    elif method == "tools/call":
        arguments = message.get("params", {}).get("arguments", {})
        write_message({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "smoke:" + str(arguments.get("text", ""))}]}})
    else:
        write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
''',
        encoding="utf-8",
    )
    backup_dir = workspace / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "README.md.bak").write_text("restored by api smoke\n", encoding="utf-8")


def run_smoke() -> list[SmokeResult]:
    results: list[SmokeResult] = []

    with tempfile.TemporaryDirectory(prefix="nanocursor-api-smoke-") as tmp:
        tmp_root = Path(tmp)
        runtime_root = tmp_root / "runtime"
        workspace_root = tmp_root / "workspaces"
        workspace = workspace_root / "project"

        os.environ["NANOCURSOR_WORKSPACE_ROOT"] = str(workspace_root)
        os.environ["NANOCURSOR_WORKSPACE_DIR"] = str(workspace)
        os.environ["NANOCURSOR_DB_PATH"] = str(tmp_root / "nanocursor.db")
        os.environ["NANOCURSOR_DEMO_EVENT_DELAY"] = "0"
        _prepare_workspace(workspace)

        from fastapi.testclient import TestClient

        import api_server
        import src.infra.config as config_module

        config_module.RUNTIME_ROOT = str(runtime_root)
        config_module.WORKSPACE_ROOT = str(workspace_root)
        original_workspace = api_server._get_workspace()
        original_run_workflow = api_server._run_workflow

        try:
            api_server._set_active_workspace(str(workspace))
            api_server._run_workflow = lambda *args, **kwargs: None

            client = TestClient(api_server.app, raise_server_exceptions=False)
            thread_id = f"smoke-{uuid.uuid4().hex[:10]}"
            api_server.event_store.create_session(thread_id, "api smoke", str(workspace), status="completed")
            api_server.event_store.append_event(
                thread_id,
                "done",
                "Smoke session ready",
                "ok",
                workspace_dir=str(workspace),
            )

            checks: list[tuple[str, str, dict[str, Any] | None, tuple[int, ...]]] = [
                ("GET", "/health", None, (200,)),
                ("GET", "/ready", None, (200,)),
                ("GET", "/version", None, (200,)),
                ("GET", "/api/system/doctor", None, (200,)),
                ("GET", "/api/system/paths", None, (200,)),
                ("GET", "/api/workspace/health", None, (200,)),
                ("GET", "/api/workspace/migration", None, (200,)),
                ("POST", "/api/workspace/migrate?dry_run=true", None, (200,)),
                ("GET", "/api/workspace/settings", None, (200,)),
                ("GET", "/api/runs/active", None, (200,)),
                ("GET", "/api/evals", None, (200,)),
                ("GET", "/api/evals/intent/catalog", None, (200,)),
                ("POST", "/api/evals/intent/run", {"case_ids": ["greeting_direct_answer"], "persist": False}, (200,)),
                ("GET", "/api/evals/agent/catalog", None, (200,)),
                (
                    "POST",
                    "/api/evals/agent/run",
                    {"suite": "core", "task_eval_ids": ["bug_fix_import_error"], "persist": False},
                    (200,),
                ),
                ("GET", "/api/evals/agent/summary", None, (200,)),
                ("GET", "/api/evals/agent/runs", None, (200,)),
                ("GET", f"/api/runs/{thread_id}/events/history", None, (200,)),
                ("GET", f"/api/runs/{thread_id}/events", None, (200,)),
                ("GET", f"/api/runs/{thread_id}/outcome", None, (200,)),
                ("GET", f"/api/runs/{thread_id}/snapshot", None, (200,)),
                ("POST", "/api/capabilities/recommend", {"prompt": "build a todo app"}, (200,)),
                ("POST", "/api/capabilities/skills", {"name": "Smoke Skill", "content": "Use smoke checks."}, (200,)),
                ("POST", "/api/capabilities/mcp/validate", {"server_id": None}, (200,)),
                (
                    "POST",
                    "/api/capabilities/mcp/servers",
                    {
                        "server_id": "smoke",
                        "command": sys.executable,
                        "args": [str(workspace / "fake_mcp_server.py")],
                        "env_keys": [],
                        "enabled": True,
                    },
                    (200,),
                ),
                (
                    "POST",
                    "/api/team/agents",
                    {
                        "name": "Smoke Agent",
                        "role": "tester",
                        "goal": "smoke",
                        "tools": [],
                        "capabilities": [],
                    },
                    (200,),
                ),
                (
                    "POST",
                    "/api/preferences",
                    {
                        "preference_type": "ui_style",
                        "content": "Prefer light UI.",
                        "importance": 7,
                    },
                    (200,),
                ),
                (
                    "POST",
                    "/api/team/agents",
                    {
                        "name": "Smoke Agent",
                        "role": "tester",
                        "goal": "smoke",
                        "tools": [],
                        "capabilities": [],
                    },
                    (400,),
                ),
                (
                    "POST",
                    "/api/preferences",
                    {
                        "preference_type": "unknown_preference",
                        "content": "bad",
                        "importance": 5,
                    },
                    (400,),
                ),
                (
                    "POST",
                    "/api/conversations/missing-conversation/team/recommend",
                    {"prompt": "build todo"},
                    (404,),
                ),
            ]

            for method, path, body, expected in checks:
                results.append(_request(client, method, path, body=body, expected=expected))

            conversation_response = client.post(
                "/api/conversations",
                json={"prompt": "smoke conversation memory", "workspace_dir": str(workspace)},
            )
            conversation_ok = conversation_response.status_code == 200 and bool(
                conversation_response.json().get("conversation", {}).get("conversation_id")
            )
            results.append(SmokeResult(
                "POST",
                "/api/conversations",
                conversation_response.status_code,
                conversation_ok,
                _response_body(conversation_response),
            ))
            if conversation_ok:
                conversation_id = conversation_response.json()["conversation"]["conversation_id"]
                from src.api.services.conversation_service import finalize_conversation_run, link_run_to_conversation

                link_run_to_conversation(
                    conversation_id,
                    thread_id,
                    str(workspace),
                    prompt="smoke updates README.md and src/api/services/conversation_service.py",
                )
                finalize_conversation_run(
                    conversation_id,
                    thread_id,
                    "completed",
                    str(workspace),
                    summary="Updated README.md and src/api/services/conversation_service.py memory smoke.",
                )
                memory_response = client.get(f"/api/conversations/{conversation_id}/memory")
                results.append(SmokeResult(
                    "GET",
                    f"/api/conversations/{conversation_id}/memory",
                    memory_response.status_code,
                    (
                        memory_response.status_code == 200
                        and memory_response.json().get("conversation_memory", {}).get("run_count", 0) >= 1
                        and "README.md" in memory_response.json().get("conversation_memory", {}).get("changed_files", [])
                    ),
                    _response_body(memory_response),
                ))
                memory_refresh = client.post(f"/api/conversations/{conversation_id}/memory/refresh")
                results.append(SmokeResult(
                    "POST",
                    f"/api/conversations/{conversation_id}/memory/refresh",
                    memory_refresh.status_code,
                    memory_refresh.status_code == 200 and memory_refresh.json().get("summary_stats", {}).get("run_count", 0) >= 1,
                    _response_body(memory_refresh),
                ))

            state_response = client.get(f"/api/runs/{thread_id}/state")
            state_ok = (
                state_response.status_code == 200
                and bool(state_response.json().get("tasks"))
                and (workspace / ".nanocursor" / "runs" / thread_id / "run_state.json").exists()
            )
            results.append(SmokeResult(
                "GET",
                f"/api/runs/{thread_id}/state",
                state_response.status_code,
                state_ok,
                _response_body(state_response),
            ))
            if state_response.status_code == 200 and state_response.json().get("tasks"):
                first_task = state_response.json()["tasks"][0]["id"]
                for method, path, body, expected in [
                    ("GET", f"/api/runs/{thread_id}/state/tasks", None, (200,)),
                    ("GET", f"/api/runs/{thread_id}/state/schedule", None, (200,)),
                    ("GET", f"/api/runs/{thread_id}/state/tasks/{first_task}/context", None, (200,)),
                    ("POST", f"/api/runs/{thread_id}/state/tasks/{first_task}/retry", None, (200,)),
                ]:
                    results.append(_request(client, method, path, body=body, expected=expected))

            loop_action_check = client.post(
                f"/api/runs/{thread_id}/loop/actions/check",
                json={
                    "action": {
                        "type": "call_tool",
                        "goal": "smoke dry-run tool action",
                        "agent": "Lead",
                        "tool_call": {"tool": "read_file", "input": {"path": "README.md"}},
                    }
                },
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/loop/actions/check",
                loop_action_check.status_code,
                (
                    loop_action_check.status_code == 200
                    and loop_action_check.json().get("allowed") is True
                    and loop_action_check.json().get("code") == "allowed"
                    and "finish_readiness" in loop_action_check.json()
                ),
                _response_body(loop_action_check),
            ))
            loop_observation = client.get(f"/api/runs/{thread_id}/loop/observation")
            results.append(SmokeResult(
                "GET",
                f"/api/runs/{thread_id}/loop/observation",
                loop_observation.status_code,
                (
                    loop_observation.status_code == 200
                    and "loop" in loop_observation.json()
                    and "task_board" in loop_observation.json()
                    and "finish_readiness" in loop_observation.json()
                ),
                _response_body(loop_observation),
            ))
            loop_step_preview = client.post(
                f"/api/runs/{thread_id}/loop/step",
                json={"commit": False, "auto_repair": True},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/loop/step",
                loop_step_preview.status_code,
                (
                    loop_step_preview.status_code == 200
                    and loop_step_preview.json().get("committed") is False
                    and bool(loop_step_preview.json().get("selected_action"))
                    and "observation" in loop_step_preview.json()
                ),
                _response_body(loop_step_preview),
            ))

            context_pack = client.get(f"/api/runs/{thread_id}/context-pack")
            context_ok = (
                context_pack.status_code == 200
                and bool(context_pack.json().get("selected_files"))
                and bool(context_pack.json().get("budget_report"))
            )
            results.append(SmokeResult(
                "GET",
                f"/api/runs/{thread_id}/context-pack",
                context_pack.status_code,
                context_ok,
                _response_body(context_pack),
            ))
            context_packs = client.get(f"/api/runs/{thread_id}/context-packs")
            packs_ok = (
                context_packs.status_code == 200
                and context_packs.json().get("total", 0) >= 1
                and bool(context_packs.json().get("context_packs"))
            )
            results.append(SmokeResult(
                "GET",
                f"/api/runs/{thread_id}/context-packs",
                context_packs.status_code,
                packs_ok,
                _response_body(context_packs),
            ))
            if packs_ok:
                pack_id = context_packs.json()["context_packs"][0]["id"]
                pack_detail = client.get(f"/api/runs/{thread_id}/context-packs/{pack_id}")
                results.append(SmokeResult(
                    "GET",
                    f"/api/runs/{thread_id}/context-packs/{pack_id}",
                    pack_detail.status_code,
                    pack_detail.status_code == 200 and pack_detail.json().get("id") == pack_id,
                    _response_body(pack_detail),
                ))
            pack_preview = client.post(
                f"/api/runs/{thread_id}/context-packs/preview",
                json={"objective": "smoke context preview"},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/context-packs/preview",
                pack_preview.status_code,
                pack_preview.status_code == 200 and pack_preview.json().get("preview") is True,
                _response_body(pack_preview),
            ))
            file_outlines = client.get("/api/workspace/file-outlines")
            results.append(SmokeResult(
                "GET",
                "/api/workspace/file-outlines",
                file_outlines.status_code,
                file_outlines.status_code == 200 and file_outlines.json().get("outline_count", 0) >= 1,
                _response_body(file_outlines),
            ))
            refresh_outlines = client.post("/api/workspace/file-outlines/refresh")
            results.append(SmokeResult(
                "POST",
                "/api/workspace/file-outlines/refresh",
                refresh_outlines.status_code,
                refresh_outlines.status_code == 200 and refresh_outlines.json().get("outline_count", 0) >= 1,
                _response_body(refresh_outlines),
            ))

            for method, path, body, expected in [
                ("POST", f"/api/runs/{thread_id}/actions/execute", {"kind": "read_file", "target": "README.md"}, (200,)),
                (
                    "POST",
                    f"/api/runs/{thread_id}/actions/execute",
                    {"kind": "write_file", "target": "smoke-action.txt", "payload": {"content": "action smoke\n"}},
                    (200,),
                ),
            ]:
                results.append(_request(client, method, path, body=body, expected=expected))

            safe_command = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={"kind": "run_command", "target": "echo smoke-command"},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/actions/execute run_command safe",
                safe_command.status_code,
                safe_command.status_code == 200
                and safe_command.json().get("requires_approval") is False
                and safe_command.json().get("permission_level") == "shell_safe"
                and "smoke-command" in safe_command.json().get("detail", {}).get("stdout", ""),
                _response_body(safe_command),
            ))

            pending_command = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={"kind": "run_command", "target": "rm -rf smoke-dist"},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/actions/execute run_command risky pending",
                pending_command.status_code,
                pending_command.status_code == 200
                and pending_command.json().get("requires_approval") is True
                and pending_command.json().get("permission_level") == "shell_risky",
                _response_body(pending_command),
            ))
            if pending_command.status_code == 200 and pending_command.json().get("approval_id"):
                from src.api.services.approval_service import resolve_tool_approval
                approval_id = pending_command.json()["approval_id"]
                resolve_tool_approval(thread_id, approval_id, True, "smoke approved", str(workspace))
                command_result = client.post(
                    f"/api/runs/{thread_id}/actions/execute",
                    json={"kind": "run_command", "target": "rm -rf smoke-dist", "approval_id": approval_id},
                )
                ok = (
                    command_result.status_code == 200
                    and command_result.json().get("result") == "success"
                    and command_result.json().get("permission_level") == "shell_risky"
                )
                results.append(SmokeResult(
                    "POST",
                    f"/api/runs/{thread_id}/actions/execute run_command risky approved",
                    command_result.status_code,
                    ok,
                    _response_body(command_result),
                ))

            suggest_agents = client.post(
                f"/api/runs/{thread_id}/agents/suggest",
                json={"prompt": "修复后端 API 并补充 pytest", "max_agents": 3},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/agents/suggest",
                suggest_agents.status_code,
                suggest_agents.status_code == 200 and bool(suggest_agents.json().get("suggestions")),
                _response_body(suggest_agents),
            ))
            if suggest_agents.status_code == 200 and suggest_agents.json().get("suggestions"):
                first_agent = suggest_agents.json()["suggestions"][0]
                spawn_agent = client.post(
                    f"/api/runs/{thread_id}/agents/spawn",
                    json={"agent": first_agent},
                )
                results.append(SmokeResult(
                    "POST",
                    f"/api/runs/{thread_id}/agents/spawn",
                    spawn_agent.status_code,
                    spawn_agent.status_code == 200 and spawn_agent.json().get("agent", {}).get("status") == "active",
                    _response_body(spawn_agent),
                ))
                if spawn_agent.status_code == 200 and spawn_agent.json().get("agent", {}).get("agent_id"):
                    agent_id = spawn_agent.json()["agent"]["agent_id"]
                    list_agents = client.get(f"/api/runs/{thread_id}/agents")
                    results.append(SmokeResult(
                        "GET",
                        f"/api/runs/{thread_id}/agents",
                        list_agents.status_code,
                        list_agents.status_code == 200 and list_agents.json().get("active_count") >= 1,
                        _response_body(list_agents),
                    ))
                    complete_agent = client.post(
                        f"/api/runs/{thread_id}/agents/{agent_id}/complete",
                        json={
                            "summary": "smoke ephemeral agent completed",
                            "evidence": [{"type": "smoke", "status": "passed"}],
                            "risks": [],
                            "artifacts": [],
                            "recommended_next_actions": [],
                        },
                    )
                    results.append(SmokeResult(
                        "POST",
                        f"/api/runs/{thread_id}/agents/{agent_id}/complete",
                        complete_agent.status_code,
                        complete_agent.status_code == 200 and complete_agent.json().get("agent", {}).get("status") == "archived",
                        _response_body(complete_agent),
                    ))
                    archived_agents = client.get(f"/api/runs/{thread_id}/agents?include_archived=true")
                    results.append(SmokeResult(
                        "GET",
                        f"/api/runs/{thread_id}/agents?include_archived=true",
                        archived_agents.status_code,
                        archived_agents.status_code == 200 and archived_agents.json().get("archived_count") >= 1,
                        _response_body(archived_agents),
                    ))

            tools_response = client.get("/api/capabilities/mcp/mcp.smoke/tools")
            tools_ok = (
                tools_response.status_code == 200
                and tools_response.json().get("ok") is True
                and any(tool.get("name") == "smoke_echo" for tool in tools_response.json().get("tools", []))
            )
            results.append(SmokeResult(
                "GET",
                "/api/capabilities/mcp/mcp.smoke/tools",
                tools_response.status_code,
                tools_ok,
                _response_body(tools_response),
            ))

            pending_mcp = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={
                    "kind": "mcp_call",
                    "target": "mcp.smoke/smoke_echo",
                    "payload": {
                        "server_id": "mcp.smoke",
                        "tool_name": "smoke_echo",
                        "arguments": {"text": "ok"},
                    },
                },
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/actions/execute mcp_call pending",
                pending_mcp.status_code,
                pending_mcp.status_code == 200 and pending_mcp.json().get("requires_approval") is True,
                _response_body(pending_mcp),
            ))
            if pending_mcp.status_code == 200 and pending_mcp.json().get("approval_id"):
                from src.api.services.approval_service import resolve_tool_approval
                approval_id = pending_mcp.json()["approval_id"]
                resolve_tool_approval(thread_id, approval_id, True, "smoke approved mcp", str(workspace))
                mcp_result = client.post(
                    f"/api/runs/{thread_id}/actions/execute",
                    json={
                        "kind": "mcp_call",
                        "target": "mcp.smoke/smoke_echo",
                        "approval_id": approval_id,
                        "payload": {
                            "server_id": "mcp.smoke",
                            "tool_name": "smoke_echo",
                            "arguments": {"text": "ok"},
                        },
                    },
                )
                content = (
                    mcp_result.json()
                    .get("detail", {})
                    .get("result", {})
                    .get("content", [{}])
                )
                ok = (
                    mcp_result.status_code == 200
                    and mcp_result.json().get("result") == "success"
                    and content
                    and content[0].get("text") == "smoke:ok"
                )
                results.append(SmokeResult(
                    "POST",
                    f"/api/runs/{thread_id}/actions/execute mcp_call approved",
                    mcp_result.status_code,
                    ok,
                    _response_body(mcp_result),
                ))

            pending_delete = client.post(
                f"/api/runs/{thread_id}/actions/execute",
                json={"kind": "delete_file", "target": "smoke-action.txt"},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/actions/execute delete_file pending",
                pending_delete.status_code,
                (
                    pending_delete.status_code == 200
                    and pending_delete.json().get("requires_approval") is True
                    and (workspace / "smoke-action.txt").exists()
                ),
                _response_body(pending_delete),
            ))
            if pending_delete.status_code == 200 and pending_delete.json().get("approval_id"):
                from src.api.services.approval_service import resolve_tool_approval
                approval_id = pending_delete.json()["approval_id"]
                resolve_tool_approval(thread_id, approval_id, True, "smoke approved delete", str(workspace))
                delete_result = client.post(
                    f"/api/runs/{thread_id}/actions/execute",
                    json={"kind": "delete_file", "target": "smoke-action.txt", "approval_id": approval_id},
                )
                ok = (
                    delete_result.status_code == 200
                    and delete_result.json().get("result") == "success"
                    and not (workspace / "smoke-action.txt").exists()
                    and Path(delete_result.json().get("detail", {}).get("trash_path", "")).exists()
                )
                results.append(SmokeResult(
                    "POST",
                    f"/api/runs/{thread_id}/actions/execute delete_file approved",
                    delete_result.status_code,
                    ok,
                    _response_body(delete_result),
                ))

            skill_id = "skill.smoke-skill"
            remediation_response = client.post(
                f"/api/runs/{thread_id}/remediation",
                json={"instruction": "Fix the smoke failure."},
            )
            results.append(SmokeResult(
                "POST",
                f"/api/runs/{thread_id}/remediation",
                remediation_response.status_code,
                remediation_response.status_code == 200,
                _response_body(remediation_response),
            ))
            if remediation_response.status_code == 200:
                retry_thread_id = remediation_response.json().get("retry_thread_id")
                if retry_thread_id:
                    api_server.run_manager.unregister(retry_thread_id)

            for method, path, body, expected in [
                ("GET", f"/api/capabilities/skills/{skill_id}", None, (200,)),
                ("POST", f"/api/capabilities/skills/{skill_id}/validate", None, (200,)),
                ("PUT", f"/api/capabilities/skills/{skill_id}", {"content": "# Smoke Skill\n\nUpdated."}, (200,)),
                ("GET", f"/api/capabilities/skills/{skill_id}/versions", None, (200,)),
                ("DELETE", f"/api/capabilities/skills/{skill_id}", None, (200,)),
            ]:
                results.append(_request(client, method, path, body=body, expected=expected))

            demo_response = client.post("/api/runs/demo", json={"prompt": "demo smoke", "workspace_dir": str(workspace)})
            results.append(SmokeResult("POST", "/api/runs/demo", demo_response.status_code, demo_response.status_code == 200, _response_body(demo_response)))
            if demo_response.status_code == 200:
                demo_thread_id = demo_response.json()["thread_id"]
                report_path = workspace / ".nanocursor" / "runs" / demo_thread_id / "report.md"
                if not report_path.exists():
                    results.append(SmokeResult("CHECK", "demo report artifact", 0, False, f"Missing {report_path}"))
                api_server.run_manager.unregister(demo_thread_id)

            checkpoint_response = client.post(
                f"/api/runs/{thread_id}/checkpoints",
                json={"target_path": "README.md"},
            )
            results.append(SmokeResult("POST", f"/api/runs/{thread_id}/checkpoints", checkpoint_response.status_code, checkpoint_response.status_code == 200, _response_body(checkpoint_response)))
            if checkpoint_response.status_code == 200:
                checkpoint_id = checkpoint_response.json()["checkpoint_id"]
                for body, expected in [({"confirmed": False}, (400,)), ({"confirmed": True}, (200,))]:
                    results.append(_request(
                        client,
                        "POST",
                        f"/api/runs/{thread_id}/checkpoints/{checkpoint_id}/restore",
                        body=body,
                        expected=expected,
                    ))
                (workspace / "README.md").write_text("mutated before run restore\n", encoding="utf-8")
                run_restore_without_confirm = client.post(
                    f"/api/runs/{thread_id}/restore",
                    json={"target_path": "README.md", "confirmed": False},
                )
                results.append(SmokeResult(
                    "POST",
                    f"/api/runs/{thread_id}/restore unconfirmed",
                    run_restore_without_confirm.status_code,
                    run_restore_without_confirm.status_code == 400,
                    _response_body(run_restore_without_confirm),
                ))
                run_restore = client.post(
                    f"/api/runs/{thread_id}/restore",
                    json={"target_path": "README.md", "confirmed": True},
                )
                results.append(SmokeResult(
                    "POST",
                    f"/api/runs/{thread_id}/restore latest_for_file",
                    run_restore.status_code,
                    (
                        run_restore.status_code == 200
                        and run_restore.json().get("restore_mode") == "latest_for_file"
                        and run_restore.json().get("filepath") == "README.md"
                        and (workspace / "README.md").read_text(encoding="utf-8") != "mutated before run restore\n"
                    ),
                    _response_body(run_restore),
                ))

            for method, path, body, expected in [
                ("GET", f"/api/runs/{thread_id}/git/status", None, (200,)),
                ("POST", f"/api/runs/{thread_id}/git/commit", {"message": "smoke"}, (200,)),
                ("POST", f"/api/runs/{thread_id}/git/discard", {"confirmed": False}, (400,)),
                ("POST", f"/api/runs/{thread_id}/recovery/actions/no-such-action", {"confirmed": True}, (400,)),
                ("POST", "/api/recovery/rollback", {"backup_name": "README.md.bak", "target_path": "README.md", "confirmed": False}, (400,)),
                ("POST", "/api/recovery/rollback", {"backup_name": "README.md.bak", "target_path": "README.md", "confirmed": True}, (200,)),
                ("GET", f"/api/runs/{thread_id}/audit", None, (200,)),
                ("GET", "/api/runs/workspace/audit", None, (200,)),
            ]:
                results.append(_request(client, method, path, body=body, expected=expected))

            settings_response = client.get("/api/workspace/settings")
            _assert_json_has(settings_response, "model", "capabilities", "runtime")
            settings = settings_response.json()
            if settings["model"].get("temperature") != 0.2 or settings["model"].get("max_tokens") != 8192:
                results.append(SmokeResult("CHECK", "workspace settings numeric values", 0, False, str(settings["model"])))

            if (workspace / "README.md").read_text(encoding="utf-8") != "restored by api smoke\n":
                results.append(SmokeResult("CHECK", "rollback restored file content", 0, False, "README.md was not restored from backup"))
        finally:
            api_server._run_workflow = original_run_workflow
            api_server._set_active_workspace(original_workspace)

    return results


def main() -> int:
    print("nanoCursor API smoke\n")
    results = run_smoke()
    failures = [result for result in results if not result.ok]
    buckets = {
        "2xx": sum(1 for result in results if 200 <= result.status_code < 300),
        "3xx": sum(1 for result in results if 300 <= result.status_code < 400),
        "4xx": sum(1 for result in results if 400 <= result.status_code < 500),
        "5xx": sum(1 for result in results if result.status_code >= 500),
        "check": sum(1 for result in results if result.status_code == 0),
    }

    for result in results:
        status = "OK" if result.ok else "FAIL"
        code = result.status_code if result.status_code else "-"
        print(f"[{status}] {result.method:6} {result.path} -> {code}")
        if result.detail and not result.ok:
            print(f"       {result.detail}")

    print()
    print(
        "Summary: "
        f"total={len(results)}, "
        f"2xx={buckets['2xx']}, "
        f"3xx={buckets['3xx']}, "
        f"4xx={buckets['4xx']}, "
        f"5xx={buckets['5xx']}, "
        f"checks={buckets['check']}"
    )
    if failures:
        print(f"{len(failures)} smoke check(s) failed.")
        return 1

    print(f"All {len(results)} smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
