from fastapi.testclient import TestClient

import api_server


def test_conversation_run_persists_execution_plan_and_events(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    async def fake_agent_loop(**kwargs):
        assert "AgentHub 动态执行编排" in kwargs["system"]
        assert "Diff 风险" in kwargs["system"]
        return "fake delivery completed"

    monkeypatch.setattr(api_server, "agent_loop", fake_agent_loop)

    created = client.post(
        "/api/conversations",
        json={"prompt": "帮我复核 Diff 风险", "workspace_dir": str(workspace)},
    )
    conversation_id = created.json()["conversation"]["conversation_id"]
    updated = client.put(
        f"/api/conversations/{conversation_id}/team",
        json={
            "workspace_dir": str(workspace),
            "members": [
                {"name": "Lead", "role": "lead"},
                {"name": "Coder", "role": "coder", "capabilities": ["tool.file_ops"]},
                {"name": "Tester", "role": "tester", "capabilities": ["skill.delivery-review"]},
                {"name": "Reviewer", "role": "reviewer", "capabilities": ["skill.delivery-review"]},
            ],
        },
    )
    assert updated.status_code == 200

    started = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"prompt": "帮我复核 Diff 风险", "workspace_dir": str(workspace)},
    )
    assert started.status_code == 200
    thread_id = started.json()["run"]["thread_id"]

    thread = api_server.active_runs[thread_id].thread
    thread.join(timeout=3)

    session = api_server.event_store.get_session(thread_id, str(workspace))
    events = api_server.event_store.list_events(thread_id, str(workspace))
    event_types = [event.type for event in events]

    assert session["execution_plan"]["strategy"] == "team_aware_run_per_message"
    assert any(stage["id"] == "diff_review" for stage in session["execution_plan"]["stages"])
    assert all(stage["status"] in {"completed", "skipped"} for stage in session["execution_plan"]["stages"])
    assert "plan_created" in event_types
    assert "orchestration_applied" in event_types
    assert "stage_updated" in event_types
    assert "done" in event_types


def test_conversation_api_respects_explicit_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    created = client.post(
        "/api/conversations",
        json={"prompt": "帮我修复前端 bug 并补充测试", "workspace_dir": str(workspace)},
    )
    assert created.status_code == 200
    conversation = created.json()["conversation"]
    conversation_id = conversation["conversation_id"]
    assert conversation["workspace_dir"] == str(workspace.resolve())
    assert len(conversation["team"]["members"]) >= 2

    updated = client.put(
        f"/api/conversations/{conversation_id}/team",
        json={
            "workspace_dir": str(workspace),
            "members": [
                {
                    "name": "Reviewer",
                    "role": "reviewer",
                    "goal": "复核本次改动",
                    "capabilities": ["skill.delivery-review"],
                }
            ],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["team"]["members"][0]["name"] == "Reviewer"

    listed = client.get("/api/conversations", params={"workspace_dir": str(workspace)})
    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["conversation_id"] == conversation_id
