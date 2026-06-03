def test_controller_direct_answer_answer_then_finish(tmp_path):
    from src.api.services.agent_loop_controller_service import run_loop_controller_step
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "controller-direct"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    first = run_loop_controller_step(thread_id, str(workspace), commit=True)
    second = run_loop_controller_step(thread_id, str(workspace), commit=False)

    assert first["committed"] is True
    assert first["selected_action"]["type"] == "answer"
    assert first["loop"]["steps"][-1]["action"]["type"] == "answer"
    assert second["committed"] is False
    assert second["selected_action"]["type"] == "finish"


def test_controller_preview_does_not_mutate_loop(tmp_path):
    from src.api.services.agent_loop_controller_service import run_loop_controller_step
    from src.api.services.agent_loop_state_service import get_agent_loop_state, init_agent_loop_state
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "controller-preview"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    result = run_loop_controller_step(thread_id, str(workspace), commit=False)

    assert result["committed"] is False
    assert result["selected_action"]["type"] == "answer"
    assert get_agent_loop_state(thread_id, str(workspace))["steps"] == []
    assert get_event_store().list_events(thread_id, str(workspace)) == []


def test_controller_auto_repairs_direct_answer_mismatch(tmp_path):
    from src.api.services.agent_loop_controller_service import run_loop_controller_step
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "controller-repair"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="哈喽",
        intent=classify_user_intent("哈喽"),
    )

    result = run_loop_controller_step(
        thread_id,
        str(workspace),
        action={"type": "create_tasks", "goal": "bad candidate", "agent": "Lead"},
        commit=True,
        auto_repair=True,
    )

    assert result["repaired"] is True
    assert result["initial_check"]["code"] == "direct_answer_action_mismatch"
    assert result["selected_action"]["type"] == "answer"
    assert result["loop"]["steps"][-1]["action"]["type"] == "answer"


def test_controller_feature_task_board_prefers_observe_not_finish(tmp_path):
    from src.api.services.agent_loop_controller_service import run_loop_controller_step
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "controller-feature-board"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我实现排序脚本",
        intent=classify_user_intent("帮我实现排序脚本"),
    )
    save_task_board(
        RunTaskBoard(
            run_id=thread_id,
            nodes=[
                RunTask(id="context", type="context_build", title="构建上下文", status="passed"),
                RunTask(id="implementation", type="implementation", title="实现排序脚本", status="running"),
            ],
        ),
        get_event_store().run_dir(thread_id, str(workspace)),
    )

    result = run_loop_controller_step(thread_id, str(workspace), commit=False)

    assert result["selected_action"]["type"] == "inspect_project"
    assert result["selected_action"]["task_id"] == "implementation"
    assert result["check"]["allowed"] is True
    assert result["observation"]["finish_readiness"]["ready"] is False


def test_loop_controller_routes_preview(tmp_path):
    import api_server
    from fastapi.testclient import TestClient

    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "controller-route"
    original_workspace = api_server._get_workspace()

    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        intent = classify_user_intent("哈喽")
        store.create_session(thread_id, "哈喽", str(workspace), status="running")
        store.update_session(
            thread_id,
            str(workspace),
            intent_decision=intent,
            execution_plan={"strategy": "lead_direct_reply", "intent_decision": intent},
        )

        client = TestClient(api_server.app)
        observation = client.get(f"/api/runs/{thread_id}/loop/observation")
        step = client.post(f"/api/runs/{thread_id}/loop/step", json={"commit": False})

        assert observation.status_code == 200
        assert observation.json()["loop"]["intent"]["route"] == "direct_answer"
        assert step.status_code == 200
        assert step.json()["committed"] is False
        assert step.json()["selected_action"]["type"] == "answer"
    finally:
        api_server._set_active_workspace(original_workspace)


def test_controller_executes_safe_call_tool_action(tmp_path):
    from src.api.services.agent_loop_controller_service import run_loop_controller_step
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# hello\n", encoding="utf-8")
    thread_id = "controller-execute-read"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="帮我看看 README",
        intent=classify_user_intent("帮我看看 README"),
    )

    result = run_loop_controller_step(
        thread_id,
        str(workspace),
        action={
            "type": "call_tool",
            "goal": "read README",
            "agent": "Lead",
            "tool_call": {"tool": "read_file", "input": {"path": "README.md", "max_chars": 20}},
        },
        commit=True,
        execute_tools=True,
    )

    assert result["committed"] is True
    assert result["tool_execution"]["executed"] is True
    assert result["tool_execution"]["result"] == "success"
    assert result["tool_execution"]["detail"]["content"] == "# hello\n"


def test_controller_pending_approval_for_risky_call_tool(tmp_path):
    from src.api.services.agent_loop_controller_service import run_loop_controller_step
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.intent_router import classify_user_intent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.py").write_text("print('old')\n", encoding="utf-8")
    thread_id = "controller-execute-pending"
    init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="删除 old.py",
        intent=classify_user_intent("删除 old.py"),
    )

    result = run_loop_controller_step(
        thread_id,
        str(workspace),
        action={
            "type": "call_tool",
            "goal": "delete old file",
            "agent": "Lead",
            "tool_call": {"tool": "delete_file", "input": {"path": "old.py"}},
        },
        commit=True,
        execute_tools=True,
    )

    assert result["tool_execution"]["result"] == "pending"
    assert result["tool_execution"]["requires_approval"] is True
    assert result["tool_execution"]["approval_id"]
    assert result["loop"]["terminal_status"] == "waiting_approval"
    assert result["loop"]["steps"][-1]["action"]["type"] == "request_approval"
