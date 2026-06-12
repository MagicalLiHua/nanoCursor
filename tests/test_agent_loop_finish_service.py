from __future__ import annotations


def _init_loop(tmp_path, thread_id: str, intent: dict):
    from src.api.services.agent_loop_state_service import init_agent_loop_state

    workspace = tmp_path / thread_id
    workspace.mkdir()
    state = init_agent_loop_state(
        thread_id,
        str(workspace),
        user_request="test request",
        intent=intent,
    )
    return workspace, state


def test_finish_readiness_direct_answer_is_ready(tmp_path):
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness

    workspace, state = _init_loop(
        tmp_path,
        "finish-direct",
        {"route": "direct_answer", "execution_route": "lead_direct_reply"},
    )

    readiness = build_loop_finish_readiness("finish-direct", str(workspace), state=state)

    assert readiness["ready"] is True
    assert readiness["mode"] == "direct_answer"


def test_finish_readiness_small_edit_without_write_is_not_ready(tmp_path):
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness

    workspace, state = _init_loop(
        tmp_path,
        "finish-small-no-write",
        {"route": "small_edit", "execution_route": "agenthub_delivery", "requires_workspace_write": True},
    )

    readiness = build_loop_finish_readiness("finish-small-no-write", str(workspace), state=state)

    assert readiness["ready"] is False
    assert readiness["mode"] == "missing_write_evidence"
    assert "call_tool" in readiness["required_actions"]


def test_finish_readiness_feature_completed_tasks_need_write_evidence(tmp_path):
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness
    from src.api.services.event_store import get_event_store
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace, state = _init_loop(
        tmp_path,
        "finish-feature-no-write",
        {"route": "feature_delivery", "execution_route": "agenthub_delivery", "requires_workspace_write": True},
    )
    save_task_board(
        RunTaskBoard(
            run_id="finish-feature-no-write",
            nodes=[
                RunTask(id="impl", type="implementation", title="实现功能", status="passed", writes_files=True),
                RunTask(id="test", type="test", title="验证功能", status="passed"),
            ],
        ),
        get_event_store().run_dir("finish-feature-no-write", str(workspace)),
    )

    readiness = build_loop_finish_readiness("finish-feature-no-write", str(workspace), state=state)

    assert readiness["ready"] is False
    assert readiness["mode"] == "missing_write_evidence"
    assert readiness["non_terminal_task_ids"] == []


def test_finish_readiness_feature_ready_with_write_diff_and_test(tmp_path):
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness
    from src.api.services.event_store import get_event_store
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace, state = _init_loop(
        tmp_path,
        "finish-feature-ready",
        {"route": "feature_delivery", "execution_route": "agenthub_delivery", "requires_workspace_write": True},
    )
    store = get_event_store()
    save_task_board(
        RunTaskBoard(
            run_id="finish-feature-ready",
            nodes=[
                RunTask(id="impl", type="implementation", title="实现功能", status="passed", writes_files=True),
                RunTask(id="test", type="test", title="验证功能", status="passed"),
            ],
        ),
        store.run_dir("finish-feature-ready", str(workspace)),
    )
    store.append_event(
        "finish-feature-ready",
        "file_changed",
        payload={"path": "src/app.py", "tool": "write_file"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        "finish-feature-ready",
        "diff_updated",
        payload={"changed_files": [{"path": "src/app.py"}]},
        workspace_dir=str(workspace),
    )
    store.append_event(
        "finish-feature-ready",
        "tool_call_finished",
        payload={"tool": "bash", "input": {"command": "pytest -q"}, "ok": True},
        workspace_dir=str(workspace),
    )

    readiness = build_loop_finish_readiness("finish-feature-ready", str(workspace), state=state)

    assert readiness["ready"] is True
    assert readiness["mode"] == "task_board_and_evidence"
    assert readiness["evidence"]["has_write_evidence"] is True
    assert readiness["evidence"]["has_test_evidence"] is True


def test_finish_readiness_blocks_failed_tasks(tmp_path):
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness
    from src.api.services.event_store import get_event_store
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace, state = _init_loop(
        tmp_path,
        "finish-failed-task",
        {"route": "debug_fix", "execution_route": "agenthub_delivery", "requires_workspace_write": True},
    )
    save_task_board(
        RunTaskBoard(
            run_id="finish-failed-task",
            nodes=[RunTask(id="fix", type="implementation", title="修复 bug", status="failed")],
        ),
        get_event_store().run_dir("finish-failed-task", str(workspace)),
    )

    readiness = build_loop_finish_readiness("finish-failed-task", str(workspace), state=state)

    assert readiness["ready"] is False
    assert readiness["mode"] == "task_board_failed"
    assert readiness["failed_task_ids"] == ["fix"]


def test_finish_readiness_blocks_pending_approval(tmp_path):
    from src.api.services.agent_loop_finish_service import build_loop_finish_readiness

    workspace, state = _init_loop(
        tmp_path,
        "finish-pending-approval",
        {
            "route": "risky_operation",
            "execution_route": "agenthub_delivery",
            "requires_workspace_write": True,
            "requires_approval": True,
        },
    )
    state.pending_approval_id = "approval-1"

    readiness = build_loop_finish_readiness("finish-pending-approval", str(workspace), state=state)

    assert readiness["ready"] is False
    assert readiness["mode"] == "approval_wait"
    assert readiness["required_actions"] == ["resolve_approval"]


def test_finish_readiness_route_returns_gate(tmp_path):
    from fastapi.testclient import TestClient

    from src.api import legacy_runtime as api_server
    from src.api.services.event_store import get_event_store

    workspace = tmp_path / "route-workspace"
    workspace.mkdir()
    thread_id = "finish-route"
    original_workspace = api_server._get_workspace()

    try:
        api_server._set_active_workspace(str(workspace))
        store = get_event_store()
        store.create_session(thread_id, "哈喽", str(workspace), status="running")
        store.update_session(
            thread_id,
            str(workspace),
            intent_decision={"route": "direct_answer", "execution_route": "lead_direct_reply"},
            execution_plan={
                "strategy": "lead_direct_reply",
                "intent_decision": {"route": "direct_answer", "execution_route": "lead_direct_reply"},
            },
        )

        client = TestClient(api_server.app)
        response = client.get(f"/api/runs/{thread_id}/loop/finish-readiness")

        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["mode"] == "direct_answer"
    finally:
        api_server._set_active_workspace(original_workspace)
