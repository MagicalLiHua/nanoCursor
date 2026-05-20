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
                ("GET", "/api/workspace/settings", None, (200,)),
                ("GET", "/api/runs/active", None, (200,)),
                ("GET", "/api/evals", None, (200,)),
                ("GET", f"/api/runs/{thread_id}/events/history", None, (200,)),
                ("GET", f"/api/runs/{thread_id}/events", None, (200,)),
                ("POST", "/api/capabilities/recommend", {"prompt": "build a todo app"}, (200,)),
                ("POST", "/api/capabilities/skills", {"name": "Smoke Skill", "content": "Use smoke checks."}, (200,)),
                ("POST", "/api/capabilities/mcp/validate", {"server_id": None}, (200,)),
                (
                    "POST",
                    "/api/capabilities/mcp/servers",
                    {
                        "server_id": "smoke",
                        "command": "python",
                        "args": ["--version"],
                        "env_keys": [],
                        "enabled": False,
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
