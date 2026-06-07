"""D6 API routes smoke tests — verify modular routes work."""

from fastapi.testclient import TestClient


def test_evals_list():
    from src.api.server import app
    client = TestClient(app)
    resp = client.get("/api/evals")
    assert resp.status_code == 200
    assert "evals" in resp.json()


def test_system_doctor():
    from src.api.server import app
    client = TestClient(app)
    resp = client.get("/api/system/doctor")
    assert resp.status_code == 200
    assert resp.json()["ok"] in (True, False)


def test_system_paths():
    from src.api.server import app
    client = TestClient(app)
    resp = client.get("/api/system/paths")
    assert resp.status_code == 200
    assert "workspace_dir" in resp.json()


def test_workspace_health():
    from src.api.server import app
    client = TestClient(app)
    resp = client.get("/api/workspace/health")
    assert resp.status_code == 200


def test_workspace_settings_accepts_numeric_model_values(tmp_path):
    from src.api import legacy_runtime as api_server

    original_workspace = api_server._get_workspace()
    workspace = tmp_path / "workspace"
    try:
        api_server._set_active_workspace(str(workspace))
        client = TestClient(api_server.app)
        resp = client.get("/api/workspace/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"]["temperature"] == 0.2
        assert data["model"]["max_tokens"] == 8192
        assert "capabilities" in data
        assert "runtime" in data
    finally:
        api_server._set_active_workspace(original_workspace)


def test_runs_active():
    from src.api.server import app
    client = TestClient(app)
    resp = client.get("/api/runs/active")
    assert resp.status_code == 200
    assert "active_runs" in resp.json()


def test_demo_run_endpoint_uses_workspace_argument_order(tmp_path, monkeypatch):
    from src.api import legacy_runtime as api_server

    original_workspace = api_server._get_workspace()
    workspace = tmp_path / "workspace"
    try:
        api_server._set_active_workspace(str(workspace))
        monkeypatch.setenv("NANOCURSOR_DEMO_EVENT_DELAY", "0")

        client = TestClient(api_server.app)
        resp = client.post("/api/runs/demo", json={"prompt": "demo smoke"})
        assert resp.status_code == 200
        thread_id = resp.json()["thread_id"]
        assert (workspace / ".nanocursor" / "runs" / thread_id / "report.md").exists()
        run_context = api_server.run_manager.get(thread_id)
        if run_context and run_context.thread:
            run_context.thread.join(timeout=2)
        assert api_server.run_manager.get(thread_id) is None
    finally:
        api_server._set_active_workspace(original_workspace)


def test_agenthub_inline_routes_match_current_request_models(tmp_path, monkeypatch):
    from src.api import legacy_runtime as api_server
    from src.api import runtime_facade
    from src.api.services.event_store import EventStore

    original_workspace = api_server._get_workspace()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    backups_dir = workspace / ".backups"
    backups_dir.mkdir(parents=True)
    (backups_dir / "README.md.bak").write_text("restored", encoding="utf-8")
    started_run_ids = []
    try:
        api_server._set_active_workspace(str(workspace))
        monkeypatch.setattr(runtime_facade, "run_workflow", lambda *args, **kwargs: None)
        store = EventStore()
        thread_id = "smoke-inline-models"
        store.create_session(thread_id, "smoke", str(workspace), status="completed")
        store.append_event(thread_id, "done", "Done", "ok", workspace_dir=str(workspace))

        client = TestClient(api_server.app)
        checks = [
            ("GET", f"/api/runs/{thread_id}/events/history", None),
            ("GET", f"/api/runs/{thread_id}/events", None),
            ("POST", "/api/capabilities/recommend", {"prompt": "build a todo app"}),
            ("POST", "/api/capabilities/skills", {"name": "Smoke Skill", "content": "Use smoke checks."}),
            ("POST", "/api/capabilities/mcp/validate", {"server_id": None}),
            ("POST", "/api/capabilities/mcp/servers", {
                "server_id": "smoke",
                "command": "python",
                "args": ["--version"],
                "env_keys": [],
                "enabled": False,
            }),
            ("POST", "/api/team/agents", {
                "name": "Smoke Agent",
                "role": "tester",
                "goal": "smoke",
                "tools": [],
                "capabilities": [],
            }),
            ("POST", "/api/preferences", {
                "preference_type": "ui_style",
                "content": "Prefer light UI.",
                "importance": 7,
            }),
        ]
        for method, path, body in checks:
            request = getattr(client, method.lower())
            resp = request(path, json=body) if body is not None else request(path)
            assert resp.status_code < 500, f"{method} {path}: {resp.text}"

        duplicate_agent_resp = client.post("/api/team/agents", json={
            "name": "Smoke Agent",
            "role": "tester",
            "goal": "smoke",
            "tools": [],
            "capabilities": [],
        })
        assert duplicate_agent_resp.status_code == 400

        invalid_preference_resp = client.post("/api/preferences", json={
            "preference_type": "unknown_preference",
            "content": "bad",
            "importance": 5,
        })
        assert invalid_preference_resp.status_code == 400

        skill_id = "skill.smoke-skill"
        for method, path, body in [
            ("GET", f"/api/capabilities/skills/{skill_id}", None),
            ("POST", f"/api/capabilities/skills/{skill_id}/validate", None),
            ("PUT", f"/api/capabilities/skills/{skill_id}", {"content": "# Smoke Skill\n\nUpdated."}),
            ("GET", f"/api/capabilities/skills/{skill_id}/versions", None),
            ("POST", f"/api/runs/{thread_id}/remediation", {"instruction": "Fix the smoke failure."}),
            ("DELETE", f"/api/capabilities/skills/{skill_id}", None),
        ]:
            request = getattr(client, method.lower())
            resp = request(path, json=body) if body is not None else request(path)
            assert resp.status_code < 500, f"{method} {path}: {resp.text}"
            if path.endswith("/remediation") and resp.status_code == 200:
                started_run_ids.append(resp.json()["retry_thread_id"])

        checkpoint_resp = client.post(
            f"/api/runs/{thread_id}/checkpoints",
            json={"target_path": "README.md"},
        )
        assert checkpoint_resp.status_code == 200
        checkpoint_id = checkpoint_resp.json()["checkpoint_id"]

        restore_without_confirm = client.post(
            f"/api/runs/{thread_id}/checkpoints/{checkpoint_id}/restore",
            json={"confirmed": False},
        )
        assert restore_without_confirm.status_code == 400

        restore_resp = client.post(
            f"/api/runs/{thread_id}/checkpoints/{checkpoint_id}/restore",
            json={"confirmed": True},
        )
        assert restore_resp.status_code == 200

        (workspace / "README.md").write_text("mutated", encoding="utf-8")
        run_restore_without_confirm = client.post(
            f"/api/runs/{thread_id}/restore",
            json={"target_path": "README.md", "confirmed": False},
        )
        assert run_restore_without_confirm.status_code == 400

        run_restore_resp = client.post(
            f"/api/runs/{thread_id}/restore",
            json={"target_path": "README.md", "confirmed": True},
        )
        assert run_restore_resp.status_code == 200
        run_restore = run_restore_resp.json()
        assert run_restore["restore_mode"] == "latest_for_file"
        assert run_restore["filepath"] == "README.md"
        assert (workspace / "README.md").read_text(encoding="utf-8") == "hello"

        git_status_resp = client.get(f"/api/runs/{thread_id}/git/status")
        assert git_status_resp.status_code == 200

        git_commit_resp = client.post(
            f"/api/runs/{thread_id}/git/commit",
            json={"message": "smoke"},
        )
        assert git_commit_resp.status_code == 200

        git_discard_resp = client.post(
            f"/api/runs/{thread_id}/git/discard",
            json={"confirmed": False},
        )
        assert git_discard_resp.status_code == 400

        bad_recovery_resp = client.post(
            f"/api/runs/{thread_id}/recovery/actions/no-such-action",
            json={"confirmed": True},
        )
        assert bad_recovery_resp.status_code == 400

        audit_resp = client.get(f"/api/runs/{thread_id}/audit")
        assert audit_resp.status_code == 200
        audit_kinds = {item["kind"] for item in audit_resp.json()["records"]}
        assert "checkpoint_create" in audit_kinds
        assert "checkpoint_restore" in audit_kinds
        assert "run_restore" in audit_kinds
        assert "git_operation" in audit_kinds
        assert "recovery_action" in audit_kinds

        rollback_without_confirm = client.post("/api/recovery/rollback", json={
            "backup_name": "README.md.bak",
            "target_path": "README.md",
            "confirmed": False,
        })
        assert rollback_without_confirm.status_code == 400

        rollback_resp = client.post("/api/recovery/rollback", json={
            "backup_name": "README.md.bak",
            "target_path": "README.md",
            "confirmed": True,
        })
        assert rollback_resp.status_code == 200
        assert (workspace / "README.md").read_text(encoding="utf-8") == "restored"

        rollback_audit_resp = client.get("/api/runs/workspace/audit")
        assert rollback_audit_resp.status_code == 200
        rollback_kinds = {item["kind"] for item in rollback_audit_resp.json()["records"]}
        assert "rollback" in rollback_kinds
    finally:
        for run_id in started_run_ids:
            api_server.run_manager.unregister(run_id)
        api_server._set_active_workspace(original_workspace)


def test_missing_conversation_team_recommend_returns_404():
    from src.api.server import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/conversations/missing-conversation/team/recommend",
        json={"prompt": "build todo"},
    )
    assert resp.status_code == 404


def test_eval_rescore_uses_result_workspace(tmp_path):
    from src.api import legacy_runtime as api_server

    original_workspace = api_server._get_workspace()
    workspace = tmp_path / "workspace"
    try:
        api_server._set_active_workspace(str(workspace))
        client = TestClient(api_server.app)
        run_resp = client.post("/api/evals/bug_fix_import_error/run")
        assert run_resp.status_code == 200
        eval_run_id = run_resp.json()["eval_run_id"]

        score_resp = client.post(
            f"/api/evals/runs/{eval_run_id}/score",
            json={"signals": {"required_events": ["plan_created", "tool_call_finished", "done"], "required_files": ["app/util.py"]}},
        )
        assert score_resp.status_code == 200
        assert score_resp.json()["overall"] == "passed"
    finally:
        api_server._set_active_workspace(original_workspace)
