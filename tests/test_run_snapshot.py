import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# snapshot workspace\n", encoding="utf-8")
    (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
    return workspace


def _client_for_workspace(workspace: Path):
    import api_server

    original_workspace = api_server._get_workspace()
    api_server._set_active_workspace(str(workspace))
    return TestClient(api_server.app), original_workspace, api_server


def test_snapshot_is_read_only_and_does_not_create_task_board(tmp_path):
    from src.api.services.event_store import get_event_store

    workspace = _workspace(tmp_path)
    thread_id = f"snapshot-readonly-{uuid.uuid4().hex}"
    client, original_workspace, api_server = _client_for_workspace(workspace)
    try:
        store = get_event_store()
        store.create_session(thread_id, "检查项目结构", str(workspace), status="running")
        store.append_event(
            thread_id,
            "agent_activity",
            title="Lead 正在分析",
            content="正在判断任务复杂度",
            agent="lead",
            payload={"current_action": "正在判断任务复杂度", "status": "running"},
            workspace_dir=str(workspace),
        )

        run_dir = workspace / ".nanocursor" / "runs" / thread_id
        assert not (run_dir / "run_state.json").exists()

        response = client.get(f"/api/runs/{thread_id}/snapshot")

        assert response.status_code == 200
        data = response.json()
        assert data["run"]["thread_id"] == thread_id
        assert data["run"]["status"] == "running"
        assert data["activity"]["current_agent"] == "lead"
        assert data["activity"]["current_action"] == "正在判断任务复杂度"
        assert data["tasks"] == []
        assert data["timeline"]
        assert not (run_dir / "run_state.json").exists()
        assert not (run_dir / "approvals").exists()
    finally:
        api_server._set_active_workspace(original_workspace)


def test_snapshot_includes_existing_task_board_and_pending_approvals(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.runtime.task_board import build_task_board, save_task_board

    workspace = _workspace(tmp_path)
    thread_id = f"snapshot-state-{uuid.uuid4().hex}"
    client, original_workspace, api_server = _client_for_workspace(workspace)
    try:
        store = get_event_store()
        store.create_session(
            thread_id,
            "实现排序算法",
            str(workspace),
            status="running",
        )
        store.update_session(
            thread_id,
            str(workspace),
            execution_plan={
                "strategy": "feature_delivery",
                "stages": [
                    {"id": "plan", "title": "分析方案", "owner_role": "planner"},
                    {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                ],
            },
        )
        run_dir = workspace / ".nanocursor" / "runs" / thread_id
        board = build_task_board(
            thread_id,
            execution_plan={
                "strategy": "feature_delivery",
                "stages": [
                    {"id": "plan", "title": "分析方案", "owner_role": "planner"},
                    {"id": "implement", "title": "代码实现", "owner_role": "coder"},
                ],
            },
        )
        save_task_board(board, run_dir)

        approvals_dir = run_dir / "approvals"
        approvals_dir.mkdir(parents=True, exist_ok=True)
        approval = {
            "id": "approval-1",
            "status": "pending",
            "risk_level": "high",
            "action": {"kind": "run_command", "target": "rm -rf build"},
        }
        (approvals_dir / "approval-1.json").write_text(
            json.dumps(approval, ensure_ascii=False),
            encoding="utf-8",
        )

        response = client.get(f"/api/runs/{thread_id}/snapshot")

        assert response.status_code == 200
        data = response.json()
        assert data["run"]["strategy"] == "feature_delivery"
        assert [task["id"] for task in data["tasks"]] == [task.id for task in board.nodes]
        assert data["approvals"] == [approval]
    finally:
        api_server._set_active_workspace(original_workspace)


def test_snapshot_includes_change_events_without_git(tmp_path):
    from src.api.services.event_store import get_event_store

    workspace = _workspace(tmp_path)
    thread_id = f"snapshot-change-{uuid.uuid4().hex}"
    client, original_workspace, api_server = _client_for_workspace(workspace)
    try:
        store = get_event_store()
        store.create_session(thread_id, "新增 benchmark.py", str(workspace), status="completed")
        store.append_event(
            thread_id,
            "file_changed",
            title="创建文件",
            content="新增 benchmark.py",
            agent="coder",
            payload={"path": "benchmark.py", "change_type": "created"},
            workspace_dir=str(workspace),
        )

        response = client.get(f"/api/runs/{thread_id}/snapshot")

        assert response.status_code == 200
        changes = response.json()["changes"]
        assert changes["files_changed"] == 1
        assert changes["files"][0]["path"] == "benchmark.py"
        assert changes["source"] in {"events", "git"}
    finally:
        api_server._set_active_workspace(original_workspace)


def test_completed_snapshot_recovers_conversation_from_durable_events(tmp_path):
    from src.api.services.event_store import get_event_store

    workspace = _workspace(tmp_path)
    thread_id = f"snapshot-conversation-{uuid.uuid4().hex}"
    client, original_workspace, api_server = _client_for_workspace(workspace)
    try:
        store = get_event_store()
        store.create_session(thread_id, "你好", str(workspace), status="completed")
        store.append_event(
            thread_id,
            "assistant_message",
            title="Lead 回复",
            content="你好，我是 nanoCursor 的 Lead Agent。",
            agent="lead",
            workspace_dir=str(workspace),
        )

        response = client.get(f"/api/runs/{thread_id}/snapshot")

        assert response.status_code == 200
        data = response.json()
        assert data["run"]["is_active"] is False
        assert data["conversation"]["messages"][0]["role"] == "user"
        assert data["conversation"]["messages"][-1]["content"] == "你好，我是 nanoCursor 的 Lead Agent。"
    finally:
        api_server._set_active_workspace(original_workspace)


def test_lead_direct_snapshot_does_not_surface_task_board(tmp_path):
    from src.api.services.event_store import get_event_store
    from src.runtime.task_board import build_task_board, save_task_board

    workspace = _workspace(tmp_path)
    thread_id = f"snapshot-direct-{uuid.uuid4().hex}"
    client, original_workspace, api_server = _client_for_workspace(workspace)
    try:
        store = get_event_store()
        store.create_session(thread_id, "哈喽", str(workspace), status="completed")
        store.update_session(
            thread_id,
            str(workspace),
            execution_plan={"strategy": "lead_direct_reply", "stages": [], "tasks": []},
        )
        run_dir = workspace / ".nanocursor" / "runs" / thread_id
        save_task_board(
            build_task_board(thread_id, execution_plan={"strategy": "lead_direct_reply"}),
            run_dir,
        )

        response = client.get(f"/api/runs/{thread_id}/snapshot")

        assert response.status_code == 200
        data = response.json()
        assert data["run"]["strategy"] == "lead_direct_reply"
        assert data["tasks"] == []
    finally:
        api_server._set_active_workspace(original_workspace)


def test_run_history_filters_by_workspace_dir(tmp_path):
    from src.api.services.event_store import get_event_store
    import api_server

    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    (workspace_a / "README.md").write_text("# a\n", encoding="utf-8")
    (workspace_b / "README.md").write_text("# b\n", encoding="utf-8")
    original_workspace = api_server._get_workspace()
    client = TestClient(api_server.app)
    try:
        store = get_event_store()
        store.create_session("run-a", "A 工作区任务", str(workspace_a), status="completed")
        store.create_session("run-b", "B 工作区任务", str(workspace_b), status="completed")

        response = client.get(f"/api/runs?workspace_dir={workspace_a}&limit=50")

        assert response.status_code == 200
        thread_ids = {item["thread_id"] for item in response.json()["runs"]}
        assert "run-a" in thread_ids
        assert "run-b" not in thread_ids
    finally:
        api_server._set_active_workspace(original_workspace)
