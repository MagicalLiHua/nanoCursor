from fastapi.testclient import TestClient


def _make_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    (workspace / "test_app.py").write_text(
        "from app import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    return workspace


def test_context_pack_explains_selected_files(tmp_path):
    from src.api.services.context_service import build_context_pack

    workspace = _make_workspace(tmp_path)
    pack = build_context_pack(
        prompt="修复 app.py 里的 add 函数并运行测试",
        workspace_dir=str(workspace),
        execution_plan={
            "strategy": "bug_fix",
            "stages": [
                {"id": "implement", "title": "实现", "capabilities": ["tool.file_ops"]},
                {"id": "verify", "title": "验证", "capabilities": ["skill.delivery-review"]},
            ],
        },
    )
    data = pack.to_dict()

    assert data["selected_files"]
    assert any(item["path"] == "app.py" for item in data["selected_files"])
    app_item = next(item for item in data["selected_files"] if item["path"] == "app.py")
    assert app_item["reasons"]
    assert app_item["relevance_score"] > 0
    assert app_item["budget_decision"] == "included"
    assert data["selection_reasons"]
    assert any("app.py" in reason for reason in data["selection_reasons"])
    assert data["budget_report"]["included_file_count"] >= 1
    assert data["budget_report"]["files"]
    assert data["token_budget"]["sections"]
    assert data["context_debug"]["memory_inputs"]["current_plan_items"] == 2
    assert data["context_debug"]["outline_cache"]["outline_count"] >= 1
    assert data["context_debug"]["selection_version"] == "context-pack-2"
    outline_cache = workspace / ".nanocursor" / "file_outlines.json"
    assert outline_cache.exists()
    assert any(item["path"] == "app.py" and item["summary"] for item in data["file_outlines"])


def test_context_pack_links_failures_to_related_files(tmp_path):
    from src.api.services.context_service import build_context_pack
    from src.api.services.event_store import get_event_store

    workspace = _make_workspace(tmp_path)
    thread_id = "context-pack-failure-file-relation"
    store = get_event_store()
    store.create_session(thread_id, "修复测试失败", str(workspace), status="failed")
    store.append_event(
        thread_id,
        "error",
        title="pytest failed",
        content="FAILED test_app.py::test_add - AssertionError: expected add(1, 2) == 4",
        agent="tester",
        payload={"task_id": "verify", "error": "test_app.py failed"},
        workspace_dir=str(workspace),
    )

    pack = build_context_pack(
        prompt="修复刚才的测试失败",
        workspace_dir=str(workspace),
        thread_id=thread_id,
        execution_plan={"strategy": "bug_fix", "stages": [{"id": "verify", "title": "验证"}]},
    )
    data = pack.to_dict()

    failure = next(item for item in data["recent_failures"] if item["id"].startswith("error-"))
    assert failure["category"] == "test_failure"
    assert "test_app.py" in failure["related_files"]
    selected_test = next(item for item in data["selected_files"] if item["path"] == "test_app.py")
    assert any("recent failure related" in reason for reason in selected_test["reasons"])
    assert data["context_debug"]["failure_context"]["included_failure_count"] >= 1
    assert data["context_debug"]["failure_context"]["related_file_count"] >= 1
    assert "关联文件: test_app.py" in pack.to_text()


def test_context_budget_preserves_p0_context_when_trimming():
    from src.agent.context_pack import ContextPack
    from src.api.services.context_budget_service import allocate_context_budget, trim_context_pack

    pack = ContextPack(
        task_summary="用户要求：修复登录按钮点击后没有响应的问题。",
        current_plan=[
            {"id": "intake", "title": "确认问题", "description": "复现登录按钮问题。"},
            {"id": "fix", "title": "局部修复", "description": "只修改登录相关代码。"},
        ],
        selected_files=[
            {
                "path": f"src/file_{index}.py",
                "relevance_score": 1.0 / index,
                "reasons": ["synthetic large candidate set"],
                "mode": "outline",
                "token_estimate": 120,
            }
            for index in range(1, 40)
        ],
        file_outlines=[
            {"path": f"src/file_{index}.py", "language": "python", "role": "source", "symbols": []}
            for index in range(1, 40)
        ],
    )

    trimmed = trim_context_pack(pack, allocate_context_budget("feature_delivery", 900))
    data = trimmed.to_dict()

    assert data["task_summary"] == "用户要求：修复登录按钮点击后没有响应的问题。"
    assert len(data["current_plan"]) == 2
    assert data["budget_report"]["trimmed_file_count"] > 0
    assert data["budget_report"]["trimmed_outline_count"] > 0
    assert data["budget_report"]["omitted_context_count"] == len(data["omitted"])
    assert any(item["kind"] == "selected_file" for item in data["omitted"])
    assert any(item["kind"] == "file_outline" for item in data["omitted"])
    assert "P0 user_request" in data["budget_report"]["protected_sections"]
    assert data["context_debug"]["protected_context"]["preserved"] is True
    assert data["context_debug"]["trimmed"]["omitted_context_count"] == len(data["omitted"])
    assert data["token_budget"]["protected_tokens_estimate"] > 0
    assert "已裁剪上下文" in trimmed.to_text()


def test_task_board_builds_parallel_analysis_and_write_lock():
    from src.runtime.task_board import build_task_board

    board = build_task_board(
        "run-board-test",
        {
            "strategy": "feature_delivery",
            "stages": [
                {"id": "plan", "title": "任务拆解", "owner_role": "planner"},
                {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                {"id": "verify", "title": "测试验证", "owner_role": "tester"},
                {"id": "diff_review", "title": "Diff 风险审查", "owner_role": "reviewer"},
            ],
            "tool_policy": {"mode": "recommend_only", "recommended_tools": ["read_file"]},
        },
    )

    node_types = {node.type for node in board.nodes}
    assert "context_build" in node_types
    assert "implementation" in node_types
    assert "test" in node_types
    assert "review" in node_types
    implementation = next(node for node in board.nodes if node.type == "implementation")
    assert implementation.writes_files is True
    assert "global:workspace_write" in implementation.resource_locks
    assert board.resources
    assert board.ready_nodes()[0].id == "node-001-intake"


def test_task_board_and_context_routes(tmp_path):
    import api_server
    from src.api.services.event_store import get_event_store

    original_workspace = api_server._get_workspace()
    workspace = _make_workspace(tmp_path)
    thread_id = "run-board-route"
    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "修复 app.py 并补测试", str(workspace), status="running")
        store.update_session(
            thread_id,
            str(workspace),
            execution_plan={
                "strategy": "feature_delivery",
                "stages": [
                    {"id": "plan", "title": "任务拆解", "owner_role": "planner"},
                    {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                    {"id": "verify", "title": "测试验证", "owner_role": "tester"},
                ],
            },
        )

        client = TestClient(api_server.app)
        state_resp = client.get(f"/api/runs/{thread_id}/state")
        assert state_resp.status_code == 200
        state = state_resp.json()
        assert state["tasks"]
        assert (workspace / ".nanocursor" / "runs" / thread_id / "run_state.json").exists()

        tasks_resp = client.get(f"/api/runs/{thread_id}/state/tasks")
        assert tasks_resp.status_code == 200
        node_id = tasks_resp.json()["tasks"][0]["id"]
        context_resp = client.get(f"/api/runs/{thread_id}/state/tasks/{node_id}/context")
        assert context_resp.status_code == 200
        context = context_resp.json()
        assert context["selected_files"]
        assert context["task"]["id"] == node_id

        debug_resp = client.get(f"/api/runs/{thread_id}/context-pack/debug")
        assert debug_resp.status_code == 200
        assert debug_resp.json()["selected_files"]

        packs_resp = client.get(f"/api/runs/{thread_id}/context-packs")
        assert packs_resp.status_code == 200
        packs = packs_resp.json()
        assert packs["total"] >= 1
        pack_id = packs["context_packs"][0]["id"]
        detail_resp = client.get(f"/api/runs/{thread_id}/context-packs/{pack_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["id"] == pack_id
        assert detail["persisted"] is True

        preview_resp = client.post(
            f"/api/runs/{thread_id}/context-packs/preview",
            json={"objective": "只预览 app.py 和测试相关上下文"},
        )
        assert preview_resp.status_code == 200
        preview = preview_resp.json()
        assert preview["preview"] is True
        assert preview["persisted"] is False
        assert preview["selection_reasons"]
        assert preview["budget_report"]["included_file_count"] >= 1

        outlines_resp = client.get("/api/workspace/file-outlines")
        assert outlines_resp.status_code == 200
        outlines = outlines_resp.json()
        assert outlines["outline_count"] >= 1
        assert "app.py" in outlines["outlines"]

        refresh_resp = client.post("/api/workspace/file-outlines/refresh")
        assert refresh_resp.status_code == 200
        assert refresh_resp.json()["outline_count"] >= 1

        retry_resp = client.post(f"/api/runs/{thread_id}/state/tasks/{node_id}/retry")
        assert retry_resp.status_code == 200
        retry_data = retry_resp.json()
        assert retry_data["recent_changes"][-1]["type"] == "task_status"

        legacy_resp = client.get(f"/api/runs/{thread_id}/graph")
        assert legacy_resp.status_code == 200
        assert legacy_resp.json()["nodes"]
    finally:
        api_server._set_active_workspace(original_workspace)


def test_run_scoped_tasks_route_is_readonly_until_state_exists(tmp_path):
    import api_server
    from src.api.services.event_store import get_event_store

    original_workspace = api_server._get_workspace()
    workspace = _make_workspace(tmp_path)
    thread_id = "run-scoped-tasks-readonly"
    run_state_path = workspace / ".nanocursor" / "runs" / thread_id / "run_state.json"
    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "实现 app.py 并补测试", str(workspace), status="running")
        store.update_session(
            thread_id,
            str(workspace),
            execution_plan={
                "strategy": "feature_delivery",
                "stages": [
                    {"id": "plan", "title": "任务拆解", "owner_role": "planner"},
                    {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                ],
            },
        )

        client = TestClient(api_server.app)
        readonly_resp = client.get(f"/api/runs/{thread_id}/tasks")
        assert readonly_resp.status_code == 200
        readonly = readonly_resp.json()
        assert readonly["source"] == "execution_plan_derived"
        assert readonly["persisted"] is False
        assert readonly["tasks"]
        assert not run_state_path.exists()

        state_resp = client.get(f"/api/runs/{thread_id}/state")
        assert state_resp.status_code == 200
        assert run_state_path.exists()

        persisted_resp = client.get(f"/api/runs/{thread_id}/tasks")
        assert persisted_resp.status_code == 200
        persisted = persisted_resp.json()
        assert persisted["source"] == "run_state"
        assert persisted["persisted"] is True
        assert persisted["total"] == len(persisted["tasks"])
    finally:
        api_server._set_active_workspace(original_workspace)


def test_run_scoped_tasks_route_hides_lead_direct_reply_tasks(tmp_path):
    import api_server
    from src.api.services.event_store import get_event_store

    original_workspace = api_server._get_workspace()
    workspace = _make_workspace(tmp_path)
    thread_id = "run-scoped-tasks-direct-reply"
    run_state_path = workspace / ".nanocursor" / "runs" / thread_id / "run_state.json"
    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "哈喽", str(workspace), status="completed")
        store.update_session(
            thread_id,
            str(workspace),
            execution_plan={"strategy": "lead_direct_reply"},
        )

        client = TestClient(api_server.app)
        resp = client.get(f"/api/runs/{thread_id}/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "lead_direct_reply"
        assert data["tasks"] == []
        assert data["total"] == 0
        assert not run_state_path.exists()

        summary_resp = client.post(f"/api/runs/{thread_id}/summaries/refresh")
        assert summary_resp.status_code == 200
        summary = summary_resp.json()
        assert summary["task_source"] == "lead_direct_reply"
        assert "tasks=0" in summary["execution_summary"]
        assert not run_state_path.exists()

        state_resp = client.get(f"/api/runs/{thread_id}/state")
        assert state_resp.status_code == 200
        state = state_resp.json()
        assert state["tasks"] == []
        assert state["metadata"]["task_board_suppressed"] is True
        assert run_state_path.exists()

        persisted_resp = client.get(f"/api/runs/{thread_id}/tasks")
        assert persisted_resp.status_code == 200
        persisted = persisted_resp.json()
        assert persisted["source"] == "lead_direct_reply"
        assert persisted["persisted"] is True
        assert persisted["tasks"] == []
        assert persisted["total"] == 0
    finally:
        api_server._set_active_workspace(original_workspace)


def test_legacy_lead_direct_reply_board_is_normalized(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_state_service import get_or_create_run_state
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace = _make_workspace(tmp_path)
    thread_id = "legacy-direct-reply-board"
    store = get_event_store()
    store.create_session(thread_id, "哈喽", str(workspace), status="completed")
    store.update_session(thread_id, str(workspace), execution_plan={"strategy": "lead_direct_reply"})
    run_dir = workspace / ".nanocursor" / "runs" / thread_id
    save_task_board(
        RunTaskBoard(
            run_id=thread_id,
            strategy="lead_direct_reply",
            nodes=[
                RunTask(id="node-001-intake", type="intake", title="接收问题", status="ready"),
                RunTask(id="node-002-direct-reply", type="direct_reply", title="直接回复"),
            ],
        ),
        run_dir,
    )

    board = get_or_create_run_state(thread_id, str(workspace))

    assert board.nodes == []
    assert board.edges == []
    assert board.metadata["task_board_suppressed"] is True
    assert board.metadata["normalized_from_legacy_direct_reply"] is True


def test_scheduler_serializes_write_nodes():
    from src.runtime.task_board import build_task_board
    from src.runtime.run_scheduler import TaskExecutionResult, apply_task_result, mark_task_running, preview_next_batch

    board = build_task_board(
        "scheduler-test",
        {
            "strategy": "feature_delivery",
            "stages": [
                {"id": "analysis_api", "title": "后端分析", "owner_role": "planner"},
                {"id": "analysis_tests", "title": "测试分析", "owner_role": "planner"},
                {"id": "implement_api", "title": "后端实现", "owner_role": "coder"},
                {"id": "implement_tests", "title": "测试实现", "owner_role": "coder"},
            ],
        },
    )

    first = preview_next_batch(board)
    assert [item["task_id"] for item in first.runnable] == ["node-001-intake"]

    start_revision = board.revision
    board = mark_task_running(board, "node-001-intake")
    assert board.revision > start_revision
    assert board.change_log[-1]["type"] == "task_started"
    board = apply_task_result(board, TaskExecutionResult(task_id="node-001-intake", status="passed"))
    assert board.change_log[-1]["type"] == "task_result"
    board = mark_task_running(board, "node-002-context")
    board = apply_task_result(board, TaskExecutionResult(task_id="node-002-context", status="passed"))

    analysis_batch = preview_next_batch(board, parallel_limit=3)
    assert len(analysis_batch.runnable) >= 2
    assert all(item["writes_files"] is False for item in analysis_batch.runnable)

    for item in analysis_batch.runnable:
        board = mark_task_running(board, item["task_id"])
        board = apply_task_result(board, TaskExecutionResult(task_id=item["task_id"], status="passed"))

    write_batch = preview_next_batch(board, parallel_limit=3)
    assert len([item for item in write_batch.runnable if item["writes_files"]]) == 1


def test_scheduler_adds_recovery_task_on_retryable_failure():
    from src.runtime.task_board import build_task_board
    from src.runtime.run_scheduler import TaskExecutionResult, apply_task_result, mark_task_running

    board = build_task_board("scheduler-recovery-test", {"strategy": "feature_delivery", "stages": []})
    board = mark_task_running(board, "node-001-intake")
    board = apply_task_result(
        board,
        TaskExecutionResult(
            task_id="node-001-intake",
            status="failed",
            summary="intake failed",
            failure_category="model_error",
            retryable=True,
        ),
    )

    recovery = board.node("node-recovery-node-001-intake")
    assert recovery is not None
    assert recovery.status == "ready"
    assert board.change_log[-1]["type"] == "task_result"
    assert any(change["type"] == "task_added" for change in board.change_log)


def test_task_schedule_and_result_routes(tmp_path):
    import api_server
    from src.api.services.event_store import get_event_store

    original_workspace = api_server._get_workspace()
    workspace = _make_workspace(tmp_path)
    thread_id = "board-scheduler-route"
    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "实现功能并测试", str(workspace), status="running")
        store.update_session(
            thread_id,
            str(workspace),
            execution_plan={
                "strategy": "feature_delivery",
                "stages": [
                    {"id": "plan", "title": "任务拆解", "owner_role": "planner"},
                    {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                    {"id": "verify", "title": "测试验证", "owner_role": "tester"},
                ],
            },
        )

        client = TestClient(api_server.app)
        schedule_resp = client.get(f"/api/runs/{thread_id}/state/schedule")
        assert schedule_resp.status_code == 200
        first_task = schedule_resp.json()["runnable"][0]["task_id"]

        start_resp = client.post(f"/api/runs/{thread_id}/state/tasks/{first_task}/start")
        assert start_resp.status_code == 200
        assert any(task["id"] == first_task and task["status"] == "running" for task in start_resp.json()["tasks"])

        result_resp = client.post(
            f"/api/runs/{thread_id}/state/tasks/{first_task}/result",
            json={"status": "passed", "summary": "intake done"},
        )
        assert result_resp.status_code == 200
        assert any(task["id"] == first_task and task["status"] == "passed" for task in result_resp.json()["tasks"])
    finally:
        api_server._set_active_workspace(original_workspace)


def test_agent_loop_run_state_patch_routes(tmp_path):
    import api_server
    from src.api.services.event_store import get_event_store

    original_workspace = api_server._get_workspace()
    workspace = _make_workspace(tmp_path)
    thread_id = "agent-loop-state-route"
    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "先分析再实现", str(workspace), status="running")

        client = TestClient(api_server.app)
        state_resp = client.get(f"/api/runs/{thread_id}/state")
        assert state_resp.status_code == 200
        initial_revision = state_resp.json()["revision"]

        patch_resp = client.patch(
            f"/api/runs/{thread_id}/state",
            json={
                "reason": "Lead loop observed failing tests",
                "add_or_update_tasks": [
                    {
                        "id": "node-loop-fix-tests",
                        "type": "recovery",
                        "title": "修复失败测试",
                        "goal": "根据测试输出局部修复实现。",
                        "agent_role": "lead",
                        "dependencies": ["node-001-intake"],
                        "can_parallel": False,
                        "writes_files": False,
                    }
                ],
                "metadata": {"loop_revision_reason": "test_failed"},
            },
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["revision"] > initial_revision
        assert any(task["id"] == "node-loop-fix-tests" for task in data["tasks"])
        assert data["metadata"]["loop_revision_reason"] == "test_failed"
        assert data["recent_changes"][-1]["type"] in {"task_added", "metadata_updated"}

        schedule_resp = client.get(f"/api/runs/{thread_id}/state/schedule")
        assert schedule_resp.status_code == 200
        assert "runnable" in schedule_resp.json()

        invalid_resp = client.patch(
            f"/api/runs/{thread_id}/state",
            json={
                "reason": "invalid task type",
                "add_or_update_tasks": [
                    {
                        "id": "bad-task",
                        "type": "not_a_real_task_type",
                        "title": "Bad task",
                    }
                ],
            },
        )
        assert invalid_resp.status_code == 400
    finally:
        api_server._set_active_workspace(original_workspace)


def test_task_domain_events_mirror_into_task_board(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_state_service import get_or_create_run_state, mirror_domain_event_to_task_board

    workspace = _make_workspace(tmp_path)
    thread_id = "domain-event-task-board"
    store = get_event_store()
    store.create_session(thread_id, "实现并验证", str(workspace), status="running")

    created = mirror_domain_event_to_task_board(
        thread_id,
        str(workspace),
        "task_created",
        {
            "task_id": "task-write-benchmark",
            "task": {
                "id": "task-write-benchmark",
                "title": "编写性能脚本",
                "description": "用 Python 实现排序算法性能对比。",
                "owner": "Coder",
                "status": "pending",
                "dependencies": ["node-001-intake"],
                "writes_files": True,
            },
        },
        agent="coder",
    )
    assert created is True
    board = get_or_create_run_state(thread_id, str(workspace))
    task = board.task("task-write-benchmark")
    assert task is not None
    assert task.type == "implementation"
    assert task.agent_role == "coder"
    assert task.writes_files is True

    updated = mirror_domain_event_to_task_board(
        thread_id,
        str(workspace),
        "task_updated",
        {"task_id": "task-write-benchmark", "status": "completed"},
        content="脚本已生成。",
        agent="lead",
    )
    assert updated is True
    board = get_or_create_run_state(thread_id, str(workspace))
    task = board.task("task-write-benchmark")
    assert task is not None
    assert task.status == "passed"
    assert any(change["type"] == "task_status" for change in board.change_log)


def test_runtime_events_attach_evidence_to_task_board(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.run_state_service import get_or_create_run_state, mirror_domain_event_to_task_board

    workspace = _make_workspace(tmp_path)
    thread_id = "runtime-evidence-task-board"
    store = get_event_store()
    store.create_session(thread_id, "实现并验证", str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={
            "strategy": "feature_delivery",
            "stages": [
                {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                {"id": "verify", "title": "测试验证", "owner_role": "tester"},
            ],
        },
    )
    board = get_or_create_run_state(thread_id, str(workspace))
    implementation = next(task for task in board.nodes if task.type == "implementation")
    tester = next(task for task in board.nodes if task.type == "test")

    assert mirror_domain_event_to_task_board(
        thread_id,
        str(workspace),
        "stage_updated",
        {"stage_id": "implement", "status": "running"},
        title="阶段状态：代码实现",
        content="pending -> running",
        agent="coder",
        event_id="event-stage",
        timestamp=1.0,
    )
    assert mirror_domain_event_to_task_board(
        thread_id,
        str(workspace),
        "tool_call_finished",
        {
            "tool": "write_file",
            "stage_id": "implement",
            "input": {"path": "app.py"},
            "output": "Wrote app.py",
            "duration_ms": 42,
            "changed_files": ["app.py"],
            "capability_trace": {"capability_id": "tool.file_ops", "capability_name": "文件读写", "agent": "Coder"},
        },
        title="能力调用：文件读写",
        content="Wrote app.py",
        agent="coder",
        event_id="event-tool",
        timestamp=2.0,
    )
    assert mirror_domain_event_to_task_board(
        thread_id,
        str(workspace),
        "file_changed",
        {"path": "app.py", "change_type": "modified", "tool": "write_file"},
        title="文件变更：app.py",
        content="modified",
        agent="coder",
        event_id="event-file",
        timestamp=3.0,
    )
    assert mirror_domain_event_to_task_board(
        thread_id,
        str(workspace),
        "test_finished",
        {"status": "passed", "checks": ["pytest"]},
        title="测试通过",
        content="1 passed",
        agent="tester",
        event_id="event-test",
        timestamp=4.0,
    )

    board = get_or_create_run_state(thread_id, str(workspace))
    implementation = board.task(implementation.id)
    tester = board.task(tester.id)
    assert implementation is not None
    assert tester is not None
    assert implementation.status == "running"
    assert {item["kind"] for item in implementation.evidence} >= {"stage", "tool_call", "file_change"}
    assert any(item.get("path") == "app.py" for item in implementation.evidence)
    tool_evidence = next(item for item in implementation.evidence if item["kind"] == "tool_call")
    assert tool_evidence["path"] == "app.py"
    assert tool_evidence["duration_ms"] == 42
    assert tool_evidence["changed_files"] == ["app.py"]
    assert any(item["kind"] == "test" for item in tester.evidence)

    task_board = board.to_task_board()
    impl_view = next(task for task in task_board["tasks"] if task["id"] == implementation.id)
    assert impl_view["evidence_count"] >= 3
    assert any(item["kind"] == "tool_call" for item in impl_view["tool_evidence"])


def test_ephemeral_agent_completion_attaches_task_output(tmp_path):
    from src.api.services.ephemeral_agent_service import complete_ephemeral_agent, spawn_ephemeral_agent
    from src.api.services.event_store import get_event_store
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _make_workspace(tmp_path)
    thread_id = "ephemeral-agent-task-output"
    store = get_event_store()
    store.create_session(thread_id, "实现后端接口并补测试", str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={
            "strategy": "feature_delivery",
            "stages": [
                {"id": "implement", "title": "后端实现", "owner_role": "coder"},
                {"id": "verify", "title": "测试验证", "owner_role": "tester"},
            ],
        },
    )
    board = get_or_create_run_state(thread_id, str(workspace))
    implementation = next(task for task in board.nodes if task.type == "implementation")

    agent = spawn_ephemeral_agent(
        thread_id,
        {
            "name": "Backend Action Agent",
            "role": "backend_worker",
            "goal": "实现后端接口。",
            "task_scope": {"include": ["src/api"], "exclude": [], "allowed_actions": ["read_file", "write_file"]},
        },
        str(workspace),
    )
    complete_ephemeral_agent(
        thread_id,
        agent["agent_id"],
        {
            "summary": "完成后端接口草案，并指出需要补充 smoke。",
            "evidence": [{"type": "file", "path": "src/api/routes/runs.py"}],
            "risks": [{"severity": "medium", "description": "需要回归测试。"}],
            "artifacts": [{"kind": "patch", "path": "src/api/routes/runs.py"}],
            "recommended_next_actions": ["运行 API smoke"],
        },
        str(workspace),
    )

    board = get_or_create_run_state(thread_id, str(workspace))
    implementation = board.task(implementation.id)
    assert implementation is not None
    assert any(item["kind"] == "agent_result" for item in implementation.evidence)
    agent_result = next(item for item in implementation.evidence if item["kind"] == "agent_result")
    assert agent_result["agent_id"] == agent["agent_id"]
    assert agent_result["artifact_count"] == 1
    assert agent_result["risk_count"] == 1
    assert any(item["kind"] == "agent_result" for item in implementation.outputs)


def test_classified_failures_create_recovery_tasks(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.api.services.failure_classifier_service import save_failures
    from src.api.services.run_state_service import get_or_create_run_state

    workspace = _make_workspace(tmp_path)
    thread_id = "failure-recovery-task-board"
    store = get_event_store()
    store.create_session(thread_id, "修复测试失败", str(workspace), status="failed")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={
            "strategy": "feature_delivery",
            "stages": [
                {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                {"id": "verify", "title": "测试验证", "owner_role": "tester"},
            ],
        },
    )
    board = get_or_create_run_state(thread_id, str(workspace))
    tester = next(task for task in board.nodes if task.type == "test")
    tester.status = "failed"
    from src.runtime.task_board import save_task_board
    save_task_board(board, workspace / ".nanocursor" / "runs" / thread_id)
    store.append_event(
        thread_id,
        "error",
        title="pytest failed",
        content="AssertionError: expected 2 == 3",
        agent="tester",
        payload={"stage_id": "verify", "task_id": tester.id, "error": "AssertionError"},
        workspace_dir=str(workspace),
    )

    failures = save_failures(thread_id, str(workspace))
    assert failures
    board = get_or_create_run_state(thread_id, str(workspace))
    recovery_tasks = [task for task in board.nodes if task.type == "recovery"]
    assert recovery_tasks
    recovery = recovery_tasks[0]
    assert tester.id in recovery.dependencies
    assert recovery.context_policy["failure_class"] == "test_failure"
    assert any(item["kind"] == "failure" for item in recovery.evidence)
    assert any(item["kind"] == "failure" for item in recovery.outputs)
