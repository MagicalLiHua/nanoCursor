"""Ephemeral sub-agent lifecycle tests."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient

from src.api.services.ephemeral_agent_service import (
    archive_ephemeral_agent,
    cleanup_expired_ephemeral_agents,
    complete_ephemeral_agent,
    list_ephemeral_agents,
    spawn_ephemeral_agent,
    suggest_ephemeral_agents,
    summarize_ephemeral_agent_contributions,
    update_ephemeral_agent_status,
)
from src.api.services.parallel_agent_service import (
    load_parallel_merge_plan,
    load_parallel_proposals,
    render_parallel_briefing,
    render_parallel_merge_guidance,
    run_parallel_agent_briefing,
    should_run_parallel_briefing,
)
from src.api.services.artifact_service import build_artifact_center
from src.api.services.delivery_service import build_delivery_contract, render_delivery_markdown
from src.api.services.event_store import EventStore
from src.api.services.report_service import build_delivery_report
from src.agent.engine import bind_runtime_context, handle_spawn_agent


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


def test_update_ephemeral_agent_status_emits_progress(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-eagent-status"

    agent = spawn_ephemeral_agent(
        thread_id,
        {"name": "Frontend Worker", "role": "frontend_worker", "goal": "检查 UI"},
        str(workspace),
    )

    updated = update_ephemeral_agent_status(thread_id, agent["agent_id"], "working", str(workspace), "正在检查布局。")

    assert updated["status"] == "working"
    assert updated["last_action"] == "正在检查布局。"
    event_types = [event.type for event in EventStore().list_events(thread_id, str(workspace))]
    assert "ephemeral_agent_updated" in event_types


def test_spawn_agent_tool_creates_run_scoped_ephemeral_agent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-spawn-agent-tool"
    EventStore().create_session(thread_id, "创建后端复核 Agent", str(workspace), status="running")

    with bind_runtime_context({"thread_id": thread_id, "workspace_dir": str(workspace), "agent": "Lead"}):
        output = asyncio.run(
            handle_spawn_agent(
                name="Backend Reviewer",
                role="backend_reviewer",
                goal="检查 FastAPI 路由和错误处理",
                reason="本轮涉及后端边界，需要独立复核。",
                tools=["read_file", "search_codebase", "read_file"],
                capabilities=["tool.project_index", "skill.delivery-review"],
                task_scope={
                    "include": ["src/api", "tests"],
                    "exclude": ["frontend"],
                    "allowed_actions": ["read_file", "search_codebase"],
                },
                expected_output={"summary_required": True, "tests_required": False},
            )
        )

    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["role"] == "backend_reviewer"
    assert payload["tools"] == ["read_file", "search_codebase"]

    agents = list_ephemeral_agents(thread_id, str(workspace))
    assert agents["active_count"] == 1
    assert agents["agents"][0]["name"] == "Backend Reviewer"

    event_types = [event.type for event in EventStore().list_events(thread_id, str(workspace))]
    assert "agent_spawn_requested" in event_types
    assert "ephemeral_agent_spawned" in event_types
    assert "agent_spawn_approved" in event_types


def test_spawn_agent_tool_requires_context_and_emits_rejection(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-spawn-agent-limit"
    EventStore().create_session(thread_id, "测试创建上限", str(workspace), status="running")

    assert asyncio.run(handle_spawn_agent("No Context", "worker", "should fail")).startswith("Error: spawn_agent requires")

    for index in range(3):
        spawn_ephemeral_agent(thread_id, {"name": f"Worker {index}", "role": f"worker_{index}"}, str(workspace))

    with bind_runtime_context({"thread_id": thread_id, "workspace_dir": str(workspace)}):
        output = asyncio.run(handle_spawn_agent("Too Many", "worker_4", "超过上限"))

    assert output.startswith("Error:")
    event_types = [event.type for event in EventStore().list_events(thread_id, str(workspace))]
    assert "agent_spawn_requested" in event_types
    assert "agent_spawn_rejected" in event_types


def test_spawn_agent_blocks_lead_direct_reply_intent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-spawn-direct-reply"
    store = EventStore()
    store.create_session(thread_id, "哈喽", str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        intent_decision={
            "route": "direct_answer",
            "execution_route": "lead_direct_reply",
            "confidence": 0.98,
            "requires_workspace_read": False,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "requires_execution": False,
            "suggested_agents": ["Lead"],
        },
    )

    with bind_runtime_context({"thread_id": thread_id, "workspace_dir": str(workspace), "agent": "Lead"}):
        output = asyncio.run(
            handle_spawn_agent(
                name="Test Agent",
                role="tester",
                goal="不应该被创建",
                tools=["read_file"],
            )
        )

    assert output.startswith("Error:")
    assert "直接回答" in output
    assert list_ephemeral_agents(thread_id, str(workspace))["active_count"] == 0


def test_read_only_intent_strips_ephemeral_write_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-spawn-read-only"
    store = EventStore()
    store.create_session(thread_id, "帮我看看项目结构", str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        intent_decision={
            "route": "read_only",
            "execution_route": "agenthub_delivery",
            "confidence": 0.9,
            "requires_workspace_read": True,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "requires_execution": True,
            "suggested_agents": ["Lead"],
        },
    )

    agent = spawn_ephemeral_agent(
        thread_id,
        {
            "name": "Read Scope Agent",
            "role": "reader",
            "goal": "只读分析",
            "task_scope": {
                "include": ["src"],
                "allowed_actions": ["read_file", "write_file", "run_command", "search_codebase"],
            },
        },
        str(workspace),
    )

    assert agent["task_scope"]["allowed_actions"] == ["read_file", "search_codebase"]
    assert agent["task_scope"]["scope_policy"]["removed_actions"] == ["write_file", "run_command"]


def test_spawn_agent_tool_run_now_submits_to_pool(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-spawn-agent-now"
    EventStore().create_session(thread_id, "运行临时 Agent", str(workspace), status="running")

    async def fake_runner(prompt, **kwargs):
        return "agent completed"

    async def run():
        from src.agent.agent_pool import get_or_create_pool, cleanup_pool
        pool = get_or_create_pool(thread_id)

        with bind_runtime_context(
            {
                "thread_id": thread_id,
                "workspace_dir": str(workspace),
                "agent": "Lead",
                "prompt": "检查后端接口风险",
                "subagent_runner": fake_runner,
                "execution_plan": {"strategy": "bug_fix", "stages": [{"id": "plan"}, {"id": "verify"}]},
            }
        ):
            output = await handle_spawn_agent(
                name="Backend Reviewer",
                role="backend_reviewer",
                goal="检查后端接口风险",
                tools=[],
                capabilities=["tool.project_index"],
                run_now=True,
            )

        payload = json.loads(output)
        assert payload["ok"] is True
        assert payload["run_now"] is True
        assert payload["status"] == "running"
        assert "pool_agent_id" in payload

        # Gather results from pool
        results = await pool.gather()
        handle = list(results.values())[0]
        assert handle.status == "completed"
        assert handle.result == "agent completed"

        cleanup_pool(thread_id)

    asyncio.run(run())


def test_parallel_agent_briefing_runs_workers_concurrently_and_archives(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-parallel-eagents"
    events = []
    starts = []

    async def fake_runner(prompt, **kwargs):
        assert "不要改文件" in prompt
        starts.append(time.perf_counter())
        await asyncio.sleep(0.05)
        return (
            "## Summary\n"
            f"- {kwargs['agent_type']} 完成只读分析，建议关注 frontend/src/App.tsx。\n"
            "## Evidence\n- checked project context and tests/test_api.py\n"
            "## Risks\n- 需要 Lead 复核最终修改范围。\n"
            "## Recommended Next Actions\n- 汇总给 Lead 后再执行写入。"
        )

    def emit_event(**kwargs):
        events.append(kwargs)

    plan = {
        "strategy": "feature_delivery",
        "stages": [{"id": "plan"}, {"id": "implement"}, {"id": "verify"}],
        "mcp_plan": [],
    }

    started = time.perf_counter()
    result = asyncio.run(
        run_parallel_agent_briefing(
            thread_id=thread_id,
            prompt="修复前端界面、后端 API，并补充测试",
            workspace_dir=str(workspace),
            execution_plan=plan,
            runner=fake_runner,
            emit_event=emit_event,
            tools=[],
            max_agents=3,
        )
    )
    elapsed = time.perf_counter() - started

    assert result["enabled"] is True
    assert len(starts) == 3
    assert elapsed < 0.13
    assert "并行子 Agent 预分析" in render_parallel_briefing(result["contributions"])
    assert any(event["event_type"] == "parallel_agents_started" for event in events)
    assert any(event["event_type"] == "parallel_agents_completed" for event in events)

    agents = list_ephemeral_agents(thread_id, str(workspace), include_archived=True)
    assert agents["active_count"] == 0
    assert agents["archived_count"] == 3
    assert result["contributions"]["summary"]["completed_count"] == 3
    assert result["proposal_artifact"]["summary"]["proposal_count"] == 3
    assert "frontend/src/App.tsx" in result["proposal_artifact"]["summary"]["suggested_files"]
    assert result["merge_plan"]["summary"]["accepted_count"] == 3
    assert {"frontend/src/App.tsx", "tests/test_api.py"} <= set(result["merge_plan"]["suggested_files"])
    assert "Lead 合并策略" in render_parallel_merge_guidance(result["merge_plan"])

    loaded = load_parallel_proposals(thread_id, str(workspace))
    assert loaded["summary"]["proposal_count"] == 3
    merge_plan = load_parallel_merge_plan(thread_id, str(workspace))
    assert merge_plan["status"] == "ready"
    assert merge_plan["summary"]["accepted_count"] == 3
    center = build_artifact_center(thread_id, str(workspace))
    artifact = next(item for item in center["artifacts"] if item["id"] == "parallel_proposals")
    assert artifact["status"] == "ready"
    assert artifact["count"] == 3
    merge_artifact = next(item for item in center["artifacts"] if item["id"] == "parallel_merge_plan")
    assert merge_artifact["status"] == "ready"
    assert merge_artifact["count"] == 3


def test_parallel_agent_briefing_skips_lead_direct_reply():
    assert should_run_parallel_briefing({"strategy": "lead_direct_reply", "stages": [{"id": "lead_reply"}]}) is False


def test_parallel_agent_briefing_archives_failed_worker_without_blocking(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "run-parallel-failed"
    events = []

    async def failing_runner(prompt, **kwargs):
        return "Error: model unavailable"

    result = asyncio.run(
        run_parallel_agent_briefing(
            thread_id=thread_id,
            prompt="修复后端 API",
            workspace_dir=str(workspace),
            execution_plan={"strategy": "bug_fix", "stages": [{"id": "plan"}, {"id": "implement"}]},
            runner=failing_runner,
            emit_event=lambda **kwargs: events.append(kwargs),
            tools=[],
            max_agents=1,
        )
    )

    assert result["enabled"] is True
    assert result["results"][0]["ok"] is False
    assert any(event["event_type"] == "parallel_agent_failed" for event in events)
    agents = list_ephemeral_agents(thread_id, str(workspace), include_archived=True)
    assert agents["archived_count"] == 1


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
    from src.api.server import app
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
