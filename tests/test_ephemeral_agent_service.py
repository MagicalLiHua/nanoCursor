"""Ephemeral sub-agent lifecycle tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.services.ephemeral_agent_service import (
    archive_ephemeral_agent,
    cleanup_expired_ephemeral_agents,
    complete_ephemeral_agent,
    list_ephemeral_agents,
    spawn_ephemeral_agent,
    suggest_ephemeral_agents,
    summarize_ephemeral_agent_contributions,
)
from src.api.services.delivery_service import build_delivery_contract, render_delivery_markdown
from src.api.services.event_store import EventStore
from src.api.services.report_service import build_delivery_report


def test_suggest_ephemeral_agents_uses_keywords_and_mcp_plan(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = suggest_ephemeral_agents(
        "修复后端 API，补充 pytest，并查看 GitHub PR",
        mcp_plan=[{"server_id": "mcp.github", "usable": True}],
        workspace_dir=str(workspace),
        max_agents=5,
    )

    roles = {item["role"] for item in result["suggestions"]}
    github = next(item for item in result["suggestions"] if item["role"] == "github_context_agent")
    assert {"backend_worker", "test_worker", "github_context_agent"} <= roles
    assert github["mcp_servers"] == ["mcp.github"]
    assert github["blocked_capabilities"] == []
    assert result["limits"]["max_active_agents"] == 3


def test_spawn_complete_auto_archives_and_writes_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-eagent"

    agent = spawn_ephemeral_agent(
        thread_id,
        {
            "name": "Backend Worker",
            "role": "backend_worker",
            "goal": "修复接口",
            "capabilities": ["tool.file_ops"],
        },
        str(workspace),
    )
    active = list_ephemeral_agents(thread_id, str(workspace))
    assert active["active_count"] == 1
    assert active["agents"][0]["agent_id"] == agent["agent_id"]

    completed = complete_ephemeral_agent(
        thread_id,
        agent["agent_id"],
        {
            "summary": "接口已修复。",
            "evidence": [{"type": "test", "status": "passed"}],
            "risks": [],
            "artifacts": [],
            "recommended_next_actions": ["交给 Reviewer"],
        },
        str(workspace),
    )

    assert completed["status"] == "archived"
    assert completed["terminal_status"] == "completed"
    assert completed["result"]["summary"] == "接口已修复。"
    assert list_ephemeral_agents(thread_id, str(workspace))["total"] == 0
    all_agents = list_ephemeral_agents(thread_id, str(workspace), include_archived=True)
    assert all_agents["archived_count"] == 1
    event_types = [event.type for event in EventStore().list_events(thread_id, str(workspace))]
    assert "ephemeral_agent_spawned" in event_types
    assert "ephemeral_agent_completed" in event_types
    assert "ephemeral_agent_archived" in event_types


def test_ephemeral_agent_contributions_feed_report_and_delivery(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-eagent-delivery"
    store = EventStore()
    store.create_session(thread_id, "修复后端 API 并补测试", str(workspace), status="completed")

    agent = spawn_ephemeral_agent(
        thread_id,
        {
            "name": "Backend Worker",
            "role": "backend_worker",
            "goal": "修复接口并整理证据",
            "capabilities": ["tool.file_ops", "skill.delivery-review"],
        },
        str(workspace),
    )
    complete_ephemeral_agent(
        thread_id,
        agent["agent_id"],
        {
            "summary": "完成接口修复，并补充 smoke 验证。",
            "evidence": [{"type": "test", "status": "passed"}],
            "risks": [{"description": "仍需人工复核边界参数。"}],
            "artifacts": [{"path": "tests/test_api.py"}],
            "recommended_next_actions": ["复核边界参数。"],
        },
        str(workspace),
    )

    summary = summarize_ephemeral_agent_contributions(thread_id, str(workspace))
    assert summary["summary"]["completed_count"] == 1
    assert summary["contributions"][0]["summary"] == "完成接口修复，并补充 smoke 验证。"
    assert summary["risks"][0]["agent_name"] == "Backend Worker"

    report = build_delivery_report(thread_id, str(workspace))
    assert "## Temporary Agent Contributions" in report["markdown"]
    assert "Backend Worker" in report["markdown"]
    assert report["agent_contributions"]["summary"]["completed_count"] == 1

    contract = build_delivery_contract(thread_id, str(workspace))
    assert contract.agent_contributions[0].name == "Backend Worker"
    assert contract.agent_contributions[0].evidence_count == 1
    assert "复核边界参数。" in contract.next_actions
    assert "Backend Worker" in render_delivery_markdown(contract)


def test_saved_report_response_appends_ephemeral_contributions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-eagent-saved-report"
    store = EventStore()
    store.create_session(thread_id, "整理 README", str(workspace), status="completed")
    run_dir = workspace / ".nanocursor" / "runs" / thread_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text("# Existing Report\n", encoding="utf-8")

    agent = spawn_ephemeral_agent(
        thread_id,
        {"name": "Docs Worker", "role": "docs_worker", "goal": "更新 README"},
        str(workspace),
    )
    complete_ephemeral_agent(
        thread_id,
        agent["agent_id"],
        {"summary": "README 作品化叙事已补齐。"},
        str(workspace),
    )

    report = build_delivery_report(thread_id, str(workspace))

    assert report["source"] == "run_artifact"
    assert "# Existing Report" in report["markdown"]
    assert "## Temporary Agent Contributions" in report["markdown"]
    assert "Docs Worker" in report["markdown"]


def test_spawn_enforces_active_limit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-limit"

    for index in range(3):
        spawn_ephemeral_agent(
            thread_id,
            {"name": f"Worker {index}", "role": f"worker_{index}"},
            str(workspace),
        )

    try:
        spawn_ephemeral_agent(thread_id, {"name": "Too Many", "role": "worker_4"}, str(workspace))
    except ValueError as exc:
        assert "上限" in str(exc)
    else:
        raise AssertionError("Expected active-agent limit to reject the fourth worker")


def test_archive_and_cleanup_expired_agents(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-cleanup"

    manual = spawn_ephemeral_agent(thread_id, {"name": "Manual", "role": "manual"}, str(workspace))
    archived = archive_ephemeral_agent(thread_id, manual["agent_id"], "不需要。", str(workspace))
    assert archived["status"] == "archived"
    assert archived["archive_reason"] == "不需要。"

    expired = spawn_ephemeral_agent(
        thread_id,
        {"name": "Expired", "role": "expired", "ttl_seconds": -1},
        str(workspace),
    )
    cleanup = cleanup_expired_ephemeral_agents(thread_id, str(workspace))
    assert cleanup["expired_count"] == 1
    assert cleanup["expired_agents"][0]["agent_id"] == expired["agent_id"]


def test_ephemeral_agent_api_lifecycle(tmp_path):
    from api_server import app
    import src.infra.config as cfg

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_workspace = cfg.WORKSPACE_DIR
    thread_id = "run-eagent-api"
    try:
        cfg.WORKSPACE_DIR = str(workspace)
        EventStore().create_session(thread_id, "修复后端 API 并补测试", str(workspace), status="running")
        client = TestClient(app, raise_server_exceptions=False)

        suggest = client.post(
            f"/api/runs/{thread_id}/agents/suggest",
            json={"prompt": "修复后端 API 并补 pytest", "max_agents": 3},
        )
        assert suggest.status_code == 200
        suggestion = suggest.json()["suggestions"][0]

        spawn = client.post(f"/api/runs/{thread_id}/agents/spawn", json={"agent": suggestion})
        assert spawn.status_code == 200
        agent_id = spawn.json()["agent"]["agent_id"]

        listed = client.get(f"/api/runs/{thread_id}/agents")
        assert listed.status_code == 200
        assert listed.json()["active_count"] == 1

        complete = client.post(
            f"/api/runs/{thread_id}/agents/{agent_id}/complete",
            json={
                "summary": "已完成。",
                "evidence": [{"type": "test", "status": "passed"}],
                "risks": [],
                "artifacts": [],
                "recommended_next_actions": [],
            },
        )
        assert complete.status_code == 200
        assert complete.json()["agent"]["status"] == "archived"

        archived = client.get(f"/api/runs/{thread_id}/agents?include_archived=true")
        assert archived.status_code == 200
        assert archived.json()["archived_count"] == 1
    finally:
        cfg.WORKSPACE_DIR = old_workspace
