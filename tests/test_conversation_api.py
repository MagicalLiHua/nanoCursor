from fastapi.testclient import TestClient

from src.api import legacy_runtime as api_server


def test_conversation_run_persists_execution_plan_and_events(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    async def fake_agent_loop_stream(**kwargs):
        assert "nanoCursor 动态执行编排" in kwargs["system"]
        assert "Diff 风险" in kwargs["system"]
        yield ("token", "fake delivery completed")
        yield ("metrics", 100, 50)
        yield ("done", "fake delivery completed")

    async def fake_parallel_briefing(**kwargs):
        return {
            "enabled": True,
            "results": [],
            "contributions": {"contributions": [], "summary": {"completed_count": 0}},
            "briefing": "",
        }

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_agent_loop_stream)
    monkeypatch.setattr(api_server, "run_parallel_agent_briefing", fake_parallel_briefing)

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
    started_body = started.json()
    thread_id = started_body["run"]["thread_id"]
    assert started_body["intent_decision"]["route"] == "review_only"
    assert started_body["intent_decision"]["execution_route"] == "agenthub_delivery"

    thread = api_server.active_runs[thread_id].thread
    thread.join(timeout=3)

    session = api_server.event_store.get_session(thread_id, str(workspace))
    events = api_server.event_store.list_events(thread_id, str(workspace))
    event_types = [event.type for event in events]

    assert "strategy" in session["execution_plan"]
    assert session["intent_decision"]["route"] == "review_only"
    assert session["execution_plan"]["intent_decision"]["route"] == "review_only"
    assert session["execution_plan"]["strategy"] in {
        "feature_delivery", "small_patch", "bug_fix", "refactor",
        "docs_only", "analysis_only",
    }
    assert any(stage["id"] == "diff_review" for stage in session["execution_plan"]["stages"])
    assert all(stage["status"] in {"completed", "skipped"} for stage in session["execution_plan"]["stages"])
    assert "plan_created" in event_types
    assert "intent_routed" in event_types
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
    assert conversation["team"]["source"] == "lead_only"
    assert [member["name"] for member in conversation["team"]["members"]] == ["Lead"]

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


def test_conversation_runs_endpoint_is_scoped_to_conversation(tmp_path):
    from src.api.services.conversation_service import (
        create_conversation,
        finalize_conversation_run,
        link_run_to_conversation,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    client = TestClient(api_server.app)

    conversation = create_conversation("", str(workspace))
    conversation_id = conversation["conversation_id"]
    link_run_to_conversation(conversation_id, "run-1", str(workspace), prompt="哈喽")
    link_run_to_conversation(conversation_id, "run-2", str(workspace), prompt="继续刚才的话题")

    other = create_conversation("其他工作区", str(other_workspace))
    link_run_to_conversation(other["conversation_id"], "other-run", str(other_workspace), prompt="不应该出现")

    resp = client.get(
        f"/api/conversations/{conversation_id}/runs",
        params={"workspace_dir": str(workspace), "limit": 10},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == conversation_id
    assert data["workspace_dir"] == str(workspace.resolve())
    assert data["current_thread_id"] == "run-2"
    assert data["run_count"] == 2
    assert [run["thread_id"] for run in data["runs"]] == ["run-2", "run-1"]
    assert all(run["thread_id"] != "other-run" for run in data["runs"])

    finalize_conversation_run(
        conversation_id,
        "run-2",
        "completed",
        str(workspace),
        summary="完成 README.md 和 src/api/services/conversation_service.py 摘要压缩。",
    )
    memory_resp = client.get(
        f"/api/conversations/{conversation_id}/memory",
        params={"workspace_dir": str(workspace)},
    )
    assert memory_resp.status_code == 200
    memory = memory_resp.json()
    assert memory["conversation_memory"]["run_count"] == 2
    assert "README.md" in memory["conversation_memory"]["changed_files"]

    refresh_resp = client.post(
        f"/api/conversations/{conversation_id}/memory/refresh",
        params={"workspace_dir": str(workspace)},
    )
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["summary_stats"]["run_count"] == 2


def test_lead_only_conversation_uses_runtime_team_without_persisting_it(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    async def fake_agent_loop_stream(**kwargs):
        yield ("token", "runtime team completed")
        yield ("metrics", 100, 50)
        yield ("done", "runtime team completed")

    async def fake_parallel_briefing(**kwargs):
        return {
            "enabled": True,
            "results": [],
            "contributions": {"contributions": [], "summary": {"completed_count": 0}},
            "briefing": "",
        }

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_agent_loop_stream)
    monkeypatch.setattr(api_server, "run_parallel_agent_briefing", fake_parallel_briefing)

    created = client.post(
        "/api/conversations",
        json={"prompt": "帮我修复前端和后端 API bug 并补测试", "workspace_dir": str(workspace)},
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["conversation_id"]
    assert [member["name"] for member in created.json()["conversation"]["team"]["members"]] == ["Lead"]

    started = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"prompt": "帮我修复前端和后端 API bug 并补测试", "workspace_dir": str(workspace)},
    )
    assert started.status_code == 200
    body = started.json()
    thread_id = body["run"]["thread_id"]
    assert body["runtime_team"]["source"] == "runtime_composed"
    assert len(body["runtime_team"]["members"]) > 1
    assert body["conversation"]["team"]["source"] == "lead_only"
    assert [member["name"] for member in body["conversation"]["team"]["members"]] == ["Lead"]

    thread = api_server.active_runs[thread_id].thread
    thread.join(timeout=3)

    session = api_server.event_store.get_session(thread_id, str(workspace))
    assert session["runtime_team_source"] == "runtime_composed"
    assert session["runtime_composition"]["complexity"]["level"] in {"small_code", "medium", "high_risk"}
    assert session["intent_decision"]["route"] == "debug_fix"
    assert session["execution_plan"]["summary"]["intent_route"] == "debug_fix"
    assert len(session["team"]) > 1


def test_short_python_generation_prompt_is_not_lead_direct_reply():
    assert api_server._is_simple_lead_message("你好，你是什么模型") is True
    assert api_server._is_simple_lead_message("帮我用python写常见的排序算法并比较性能") is False


def test_lead_only_execution_plan_uses_canonical_restrictive_tool_policy(tmp_path):
    from src.api.services.conversation_run_service import lead_only_execution_plan
    from src.runtime.tool_policy_runtime import ToolPolicyRuntime

    plan = lead_only_execution_plan("哈喽", str(tmp_path), [{"name": "Lead", "role": "lead"}])
    policy = plan["tool_policy"]
    runtime = ToolPolicyRuntime(policy=policy)

    assert policy["mode"] == "enforced"
    assert policy["allowed_tools"] == ["recall_memories"]
    assert "edit_file" in policy["denied_tools"]
    assert "requires_approval" not in policy
    assert "blocked_tools" not in policy
    assert runtime.check("recall_memories").allowed is True
    assert runtime.check("edit_file", {"path": "README.md"}).allowed is False
    assert runtime.check("bash", {"command": "ls"}).allowed is False


def test_direct_answer_loop_state_stays_minimal(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = TestClient(api_server.app)

    async def fake_agent_loop_stream(**kwargs):
        yield ("token", "哈喽！有什么可以帮你？")
        yield ("metrics", 10, 8)
        yield ("done", "哈喽！有什么可以帮你？")

    async def fake_parallel_briefing(**kwargs):
        return {
            "enabled": False,
            "results": [],
            "contributions": {"contributions": [], "summary": {"completed_count": 0}},
            "briefing": "",
        }

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_agent_loop_stream)
    monkeypatch.setattr(api_server, "run_parallel_agent_briefing", fake_parallel_briefing)

    created = client.post(
        "/api/conversations",
        json={"prompt": "哈喽", "workspace_dir": str(workspace)},
    )
    conversation_id = created.json()["conversation"]["conversation_id"]
    started = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"prompt": "哈喽", "workspace_dir": str(workspace)},
    )
    assert started.status_code == 200
    thread_id = started.json()["run"]["thread_id"]

    api_server.active_runs[thread_id].thread.join(timeout=3)

    loop = client.get(f"/api/runs/{thread_id}/loop")
    assert loop.status_code == 200
    actions = [step["action"]["type"] for step in loop.json()["steps"]]
    assert actions == ["answer", "finish"]
    assert loop.json()["terminal_status"] == "completed"


def test_read_only_run_uses_controller_loop_and_read_only_tools(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    client = TestClient(api_server.app)
    observed_tool_names = []

    async def fake_agent_loop_stream(**kwargs):
        observed_tool_names.extend(tool.get("name") for tool in kwargs.get("tools", []))
        yield ("token", "README 标题是 demo。")
        yield ("metrics", 12, 8)
        yield ("done", "README 标题是 demo。")

    async def fake_parallel_briefing(**kwargs):
        raise AssertionError("read_only runtime turns should not create parallel briefing agents")

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_agent_loop_stream)
    monkeypatch.setattr(api_server, "run_parallel_agent_briefing", fake_parallel_briefing)

    prompt = "查看当前项目结构"
    created = client.post(
        "/api/conversations",
        json={"prompt": prompt, "workspace_dir": str(workspace)},
    )
    conversation_id = created.json()["conversation"]["conversation_id"]
    started = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"prompt": prompt, "workspace_dir": str(workspace)},
    )
    assert started.status_code == 200
    thread_id = started.json()["run"]["thread_id"]

    api_server.active_runs[thread_id].thread.join(timeout=3)

    loop = client.get(f"/api/runs/{thread_id}/loop")
    assert loop.status_code == 200
    actions = [step["action"]["type"] for step in loop.json()["steps"]]
    assert actions == ["inspect_project", "answer", "finish"]
    assert "write_file" not in observed_tool_names
    assert "edit_file" not in observed_tool_names
    assert "spawn_agent" not in observed_tool_names


def test_small_edit_run_requires_real_change_and_uses_bounded_tools(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readme = workspace / "README.md"
    readme.write_text("helo\n", encoding="utf-8")
    client = TestClient(api_server.app)
    observed_tool_names = []

    async def fake_agent_loop_stream(**kwargs):
        observed_tool_names.extend(tool.get("name") for tool in kwargs.get("tools", []))
        tool_input = {"path": "README.md", "content": "hello\n"}
        decision = await kwargs["on_tool_check"]("write_file", tool_input)
        assert decision.allowed is True
        readme.write_text("hello\n", encoding="utf-8")
        kwargs["on_tool_call"]("write_file", tool_input, "Updated README.md")
        yield ("token", "已修正 README 中的拼写。")
        yield ("metrics", 15, 10)
        yield ("done", "已修正 README 中的拼写。")

    async def fake_parallel_briefing(**kwargs):
        raise AssertionError("small_edit runtime turns should not create parallel briefing agents")

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_agent_loop_stream)
    monkeypatch.setattr(api_server, "run_parallel_agent_briefing", fake_parallel_briefing)

    created = client.post(
        "/api/conversations",
        json={"prompt": "帮我改 README 的错别字", "workspace_dir": str(workspace)},
    )
    conversation_id = created.json()["conversation"]["conversation_id"]
    started = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"prompt": "帮我改 README 的错别字", "workspace_dir": str(workspace)},
    )
    assert started.status_code == 200
    thread_id = started.json()["run"]["thread_id"]

    api_server.active_runs[thread_id].thread.join(timeout=3)

    loop = client.get(f"/api/runs/{thread_id}/loop")
    assert loop.status_code == 200
    actions = [step["action"]["type"] for step in loop.json()["steps"]]
    assert actions == ["inspect_project", "call_tool", "run_checks", "summarize", "finish"]
    assert loop.json()["terminal_status"] == "completed"
    assert readme.read_text(encoding="utf-8") == "hello\n"
    assert "write_file" in observed_tool_names
    assert "bash" not in observed_tool_names
    assert "delete_file" not in observed_tool_names
    assert "spawn_agent" not in observed_tool_names


def test_small_edit_run_fails_when_model_only_claims_completion(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("helo\n", encoding="utf-8")
    client = TestClient(api_server.app)

    async def fake_agent_loop_stream(**kwargs):
        yield ("token", "已修正 README 中的拼写。")
        yield ("metrics", 8, 8)
        yield ("done", "已修正 README 中的拼写。")

    monkeypatch.setattr(api_server, "agent_loop_stream", fake_agent_loop_stream)

    created = client.post(
        "/api/conversations",
        json={"prompt": "帮我改 README 的错别字", "workspace_dir": str(workspace)},
    )
    conversation_id = created.json()["conversation"]["conversation_id"]
    started = client.post(
        f"/api/conversations/{conversation_id}/runs",
        json={"prompt": "帮我改 README 的错别字", "workspace_dir": str(workspace)},
    )
    thread_id = started.json()["run"]["thread_id"]

    api_server.active_runs[thread_id].thread.join(timeout=3)

    loop = client.get(f"/api/runs/{thread_id}/loop")
    actions = [step["action"]["type"] for step in loop.json()["steps"]]
    session = api_server.event_store.get_session(thread_id, str(workspace))
    assert actions == ["inspect_project", "fail"]
    assert loop.json()["terminal_status"] == "failed"
    assert session["status"] == "failed"
    assert "未检测到本轮成功写入工具调用" in session["error"]


def test_lead_agent_route_creates_permanent_and_temporary_agents(tmp_path):
    import src.infra.config as cfg

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_workspace = cfg.WORKSPACE_DIR
    client = TestClient(api_server.app)
    thread_id = "run-lead-agent-route"

    try:
        api_server._set_active_workspace(str(workspace))
        api_server.event_store.create_session(thread_id, "修复后端并补测试", str(workspace), status="running")

        permanent = client.post(
            "/api/lead/agents",
            json={
                "name": "Security Reviewer",
                "role": "security reviewer",
                "goal": "复核高风险文件修改。",
                "tools": ["diff", "risk_review"],
                "capabilities": ["skill.delivery-review"],
                "lifetime": "permanent",
            },
        )
        assert permanent.status_code == 200
        assert permanent.json()["lifetime"] == "permanent"
        assert permanent.json()["agent"]["source"] == "lead"

        temporary = client.post(
            "/api/lead/agents",
            json={
                "name": "Backend Temp",
                "role": "backend_temp",
                "goal": "本轮只检查 FastAPI 路由。",
                "tools": ["read_file", "run_command"],
                "capabilities": ["tool.project_index"],
                "lifetime": "temporary",
                "thread_id": thread_id,
                "task_scope": {"include": ["src/api"], "exclude": ["frontend"]},
                "expected_output": {"summary_required": True, "tests_required": True},
            },
        )
        assert temporary.status_code == 200
        agent = temporary.json()["agent"]
        assert temporary.json()["lifetime"] == "temporary"
        assert agent["thread_id"] == thread_id
        assert agent["tools"] == ["read_file", "run_command"]
        assert agent["task_scope"]["allowed_actions"] == ["read_file", "run_command"]
    finally:
        api_server._set_active_workspace(old_workspace)
