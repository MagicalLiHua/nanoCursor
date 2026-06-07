from fastapi.testclient import TestClient


def test_agent_loop_state_persists_steps(tmp_path):
    from src.api.services.agent_loop_state_service import (
        append_loop_step,
        finalize_agent_loop_state,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-state-run"
    intent = classify_user_intent("帮我看看这个项目结构")

    state = init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我看看这个项目结构",
        intent=intent,
        conversation_id="conv-loop",
    )
    assert state.intent.route == "read_only"
    assert state.current_step == 0

    state = append_loop_step(
        thread_id,
        str(workspace),
        phase="observe",
        action={
            "type": "inspect_project",
            "goal": "读取项目结构。",
            "agent": "Lead",
        },
        summary="已准备只读检查。",
    )
    assert state.current_step == 1
    assert state.steps[0].action.type == "inspect_project"
    assert state.terminal_status is None

    state = finalize_agent_loop_state(
        thread_id,
        str(workspace),
        status="completed",
        final_message="项目结构已总结。",
    )
    assert state.terminal_status == "completed"
    assert state.steps[-1].action.type == "finish"

    loaded = get_agent_loop_state(thread_id, str(workspace))
    assert loaded["thread_id"] == thread_id
    assert loaded["current_step"] == 2
    assert loaded["intent"]["route"] == "read_only"


def test_run_loop_route_derives_state_from_session(tmp_path):
    from src.api import legacy_runtime as api_server
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-route-run"
    original_workspace = api_server._get_workspace()

    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "哈喽", str(workspace), status="completed")
        store.update_session(
            thread_id,
            str(workspace),
            conversation_id="conv-route",
            intent_decision=classify_user_intent("哈喽"),
        )

        client = TestClient(api_server.app)
        resp = client.get(f"/api/runs/{thread_id}/loop")

        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == thread_id
        assert data["conversation_id"] == "conv-route"
        assert data["intent"]["route"] == "direct_answer"
        assert data["steps"] == []
    finally:
        api_server._set_active_workspace(original_workspace)


def test_loop_guard_blocks_direct_answer_write_tools(tmp_path):
    from src.api.services.agent_loop_state_service import (
        check_loop_tool_guard,
        init_agent_loop_state,
    )
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-direct-guard"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    read_decision = check_loop_tool_guard(thread_id, str(workspace), "read_file", {"path": "README.md"})
    assert read_decision is None

    write_decision = check_loop_tool_guard(thread_id, str(workspace), "write_file", {"path": "README.md"})
    assert write_decision is not None
    assert write_decision.allowed is False
    assert write_decision.permission_level == "safe_write"


def test_loop_guard_blocks_read_only_action_execute(tmp_path):
    from src.api.services.action_execution_service import execute_action
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-readonly-action-guard"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我看看这个项目结构",
        intent=classify_user_intent("帮我看看这个项目结构"),
    )

    result = execute_action(
        "write_file",
        "README.md",
        {"content": "blocked"},
        thread_id=thread_id,
        workspace_dir=str(workspace),
    )

    assert result["result"] == "failure"
    assert result["allowed"] is False
    assert "非写入任务" in result["reason"]
    assert not (workspace / "README.md").exists()


def test_loop_guard_blocks_small_edit_risky_shell(tmp_path):
    from src.api.services.agent_loop_state_service import (
        check_loop_action,
        check_loop_tool_guard,
        init_agent_loop_state,
    )
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "small-edit-risky-shell"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我改 README 的错别字",
        intent=classify_user_intent("帮我改 README 的错别字"),
    )

    tool_decision = check_loop_tool_guard(
        thread_id,
        str(workspace),
        "bash",
        {"command": "rm -rf build"},
    )
    action_decision = check_loop_action(
        thread_id,
        str(workspace),
        {
            "type": "call_tool",
            "goal": "remove build",
            "agent": "Lead",
            "tool_call": {"tool": "bash", "input": {"command": "rm -rf build"}},
        },
    )

    assert tool_decision is not None
    assert tool_decision.allowed is False
    assert action_decision["allowed"] is False
    assert action_decision["code"] == "small_edit_tool_action_mismatch"
    assert action_decision["repaired_action"]["type"] == "inspect_project"


def test_loop_guard_allows_read_only_mcp_action(tmp_path):
    from src.api.services.action_execution_service import check_and_decide
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-readonly-mcp-read"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我看看 GitHub issues",
        intent=classify_user_intent("帮我看看 GitHub issues"),
    )

    result = check_and_decide(
        "mcp_call",
        "mcp.github/list_issues",
        thread_id=thread_id,
        workspace_dir=str(workspace),
        payload={"tool_name": "list_issues"},
    )

    assert result["allowed"] is True
    assert result["requires_approval"] is False
    assert result["permission_level"] == "mcp_read"


def test_loop_guard_blocks_read_only_mcp_write_action(tmp_path):
    from src.api.services.action_execution_service import check_and_decide
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-readonly-mcp-write"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我看看 GitHub issues",
        intent=classify_user_intent("帮我看看 GitHub issues"),
    )

    result = check_and_decide(
        "mcp_call",
        "mcp.github/create_issue",
        thread_id=thread_id,
        workspace_dir=str(workspace),
        payload={"tool_name": "create_issue"},
    )

    assert result["allowed"] is False
    assert result["permission_level"] == "mcp_write"
    assert "非写入任务" in result["reason"]


def test_loop_step_limit_fuses_run(tmp_path):
    from src.api.services.agent_loop_state_service import (
        LoopStepLimitExceeded,
        append_loop_step,
        check_loop_can_continue,
        init_agent_loop_state,
    )
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-step-limit"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我看看这个项目结构",
        intent=classify_user_intent("帮我看看这个项目结构"),
        max_steps=1,
    )
    append_loop_step(
        thread_id,
        str(workspace),
        action={"type": "inspect_project", "goal": "inspect", "agent": "Lead"},
    )

    decision = check_loop_can_continue(thread_id, str(workspace))
    assert decision is not None
    assert decision.allowed is False
    assert decision.permission_level == "loop_step_limit"

    try:
        append_loop_step(
            thread_id,
            str(workspace),
            action={"type": "call_tool", "goal": "too much", "agent": "Lead"},
        )
    except LoopStepLimitExceeded:
        pass
    else:
        raise AssertionError("Expected LoopStepLimitExceeded")


def test_loop_guard_allows_feature_write_action(tmp_path):
    from src.api.services.action_execution_service import execute_action
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-feature-write"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我写一个 README",
        intent=classify_user_intent("帮我写一个 README"),
    )

    result = execute_action(
        "write_file",
        "README.md",
        {"content": "# ok\n"},
        thread_id=thread_id,
        workspace_dir=str(workspace),
    )

    assert result["result"] == "success"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "# ok\n"


def test_loop_finish_readiness_for_direct_answer_has_no_task_board_requirement(tmp_path):
    from src.api.services.agent_loop_state_service import (
        assess_loop_finish_readiness,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-direct-finish-ready"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    readiness = assess_loop_finish_readiness(thread_id, str(workspace))
    state = get_agent_loop_state(thread_id, str(workspace))

    assert readiness["ready"] is True
    assert readiness["mode"] == "direct_answer"
    assert state["finish_readiness"]["ready"] is True
    assert state["next_actions"] == ["answer", "finish"]


def test_loop_finish_readiness_reports_unfinished_task_board(tmp_path):
    from src.api.services.agent_loop_state_service import (
        assess_loop_finish_readiness,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-unfinished-board"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我实现一个排序工具",
        intent=classify_user_intent("帮我实现一个排序工具"),
    )
    run_dir = get_event_store().run_dir(thread_id, str(workspace))
    save_task_board(
        RunTaskBoard(
            run_id=thread_id,
            nodes=[
                RunTask(id="context", type="context_build", title="构建上下文", status="passed"),
                RunTask(id="implementation", type="implementation", title="实现排序工具", status="running"),
                RunTask(id="review", type="review", title="复核结果", status="pending"),
            ],
        ),
        run_dir,
    )

    readiness = assess_loop_finish_readiness(thread_id, str(workspace))
    state = get_agent_loop_state(thread_id, str(workspace))

    assert readiness["ready"] is False
    assert readiness["counts"]["running"] == 1
    assert readiness["counts"]["pending"] == 1
    assert readiness["non_terminal_task_ids"] == ["implementation", "review"]
    assert state["finish_readiness"]["ready"] is False
    assert state["next_actions"] == ["observe", "wait_for_tool", "verify"]


def test_loop_finish_readiness_allows_successful_terminal_task_board(tmp_path):
    from src.api.services.agent_loop_state_service import (
        assess_loop_finish_readiness,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-ready-board"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我实现一个排序工具",
        intent=classify_user_intent("帮我实现一个排序工具"),
    )
    run_dir = get_event_store().run_dir(thread_id, str(workspace))
    save_task_board(
        RunTaskBoard(
            run_id=thread_id,
            nodes=[
                RunTask(id="context", type="context_build", title="构建上下文", status="passed"),
                RunTask(id="implementation", type="implementation", title="实现排序工具", status="passed"),
                RunTask(id="review", type="review", title="复核结果", status="skipped"),
            ],
        ),
        run_dir,
    )

    readiness = assess_loop_finish_readiness(thread_id, str(workspace))
    state = get_agent_loop_state(thread_id, str(workspace))

    assert readiness["ready"] is True
    assert readiness["counts"]["passed"] == 2
    assert readiness["counts"]["skipped"] == 1
    assert readiness["non_terminal_task_ids"] == []
    assert state["next_actions"] == ["summarize", "finish"]


def test_loop_action_gate_blocks_direct_answer_task_creation(tmp_path):
    from src.api.services.agent_loop_state_service import (
        LoopActionRejected,
        append_loop_step,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-direct-action-gate"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    try:
        append_loop_step(
            thread_id,
            str(workspace),
            action={"type": "create_tasks", "goal": "should not happen", "agent": "Lead"},
        )
    except LoopActionRejected as exc:
        assert "lead_direct_reply" in str(exc)
    else:
        raise AssertionError("Expected LoopActionRejected")

    state = get_agent_loop_state(thread_id, str(workspace))
    assert state["steps"] == []
    events = get_event_store().list_events(thread_id, str(workspace))
    assert events[-1].type == "agent_loop_action_rejected"
    assert events[-1].payload["gate"]["code"] == "direct_answer_action_mismatch"


def test_loop_action_gate_requires_tool_call_payload(tmp_path):
    from src.api.services.agent_loop_state_service import (
        LoopActionRejected,
        append_loop_step,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-tool-payload-gate"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我写一个 README",
        intent=classify_user_intent("帮我写一个 README"),
    )

    try:
        append_loop_step(
            thread_id,
            str(workspace),
            action={"type": "call_tool", "goal": "missing tool payload", "agent": "Lead"},
        )
    except LoopActionRejected as exc:
        assert "tool_call" in str(exc)
    else:
        raise AssertionError("Expected LoopActionRejected")

    state = get_agent_loop_state(thread_id, str(workspace))
    assert state["steps"] == []


def test_finish_step_attaches_readiness_warning_for_unfinished_board(tmp_path):
    from src.api.services.agent_loop_state_service import (
        append_loop_step,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-finish-warning"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我实现一个排序工具",
        intent=classify_user_intent("帮我实现一个排序工具"),
    )
    save_task_board(
        RunTaskBoard(
            run_id=thread_id,
            nodes=[
                RunTask(id="implementation", type="implementation", title="实现排序工具", status="running"),
            ],
        ),
        get_event_store().run_dir(thread_id, str(workspace)),
    )

    state = append_loop_step(
        thread_id,
        str(workspace),
        action={"type": "finish", "goal": "finish anyway", "agent": "Lead"},
    )

    step = state.steps[-1]
    assert "Finish readiness warning" in step.summary
    assert step.action.context_requirements["finish_readiness"]["ready"] is False
    loaded = get_agent_loop_state(thread_id, str(workspace))
    assert loaded["terminal_status"] == "completed"


def test_check_loop_action_is_dry_run_and_suggests_repair(tmp_path):
    from src.api.services.agent_loop_state_service import (
        check_loop_action,
        get_agent_loop_state,
        init_agent_loop_state,
    )
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-action-dry-run"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    result = check_loop_action(
        thread_id,
        str(workspace),
        {"type": "create_tasks", "goal": "should be repaired", "agent": "Lead"},
    )

    assert result["allowed"] is False
    assert result["code"] == "direct_answer_action_mismatch"
    assert result["repaired_action"]["type"] == "answer"
    assert result["next_actions"] == ["answer", "finish"]
    assert get_agent_loop_state(thread_id, str(workspace))["steps"] == []
    assert get_event_store().list_events(thread_id, str(workspace)) == []


def test_check_loop_action_allows_valid_tool_action(tmp_path):
    from src.api.services.agent_loop_state_service import check_loop_action, init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-action-valid-tool"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我写一个 README",
        intent=classify_user_intent("帮我写一个 README"),
    )

    result = check_loop_action(
        thread_id,
        str(workspace),
        {
            "type": "call_tool",
            "goal": "write README",
            "agent": "Lead",
            "tool_call": {"tool": "write_file", "input": {"path": "README.md"}},
        },
    )

    assert result["allowed"] is True
    assert result["code"] == "allowed"
    assert result["repaired_action"] is None
    assert result["finish_readiness"]["ready"] is True


def test_check_loop_action_reports_schema_errors(tmp_path):
    from src.api.services.agent_loop_state_service import check_loop_action, init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "loop-action-schema-error"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我写一个 README",
        intent=classify_user_intent("帮我写一个 README"),
    )

    result = check_loop_action(
        thread_id,
        str(workspace),
        {"type": "not_a_real_action", "goal": "bad action"},
    )

    assert result["allowed"] is False
    assert result["code"] == "invalid_action_schema"
    assert result["schema_errors"]
