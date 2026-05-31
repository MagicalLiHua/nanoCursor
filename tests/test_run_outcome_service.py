import subprocess

from fastapi.testclient import TestClient

import api_server
from src.api.services.event_store import get_event_store
from src.api.services.run_outcome_service import build_run_outcome


def _commit_initial(repo):
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def test_run_outcome_marks_lightweight_reply_without_fake_report(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    thread_id = "outcome-lightweight"
    store.create_session(thread_id, "你好", str(workspace), status="completed")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={
            "strategy": "lead_direct_reply",
            "stages": [{"id": "lead_reply", "title": "Lead 直接回复", "status": "completed"}],
            "tasks": [{"id": "stage-01-lead_reply", "title": "Lead 直接回复", "status": "completed"}],
        },
        team=[{"name": "Lead", "role": "lead"}],
    )
    store.append_event(
        thread_id,
        "assistant_message",
        content="你好，我在。",
        agent="lead",
        workspace_dir=str(workspace),
    )

    outcome = build_run_outcome(thread_id, str(workspace))

    assert outcome["status"] == "completed"
    assert outcome["strategy"] == "lead_direct_reply"
    assert outcome["summary"]["final_message"] == "你好，我在。"
    assert outcome["summary"]["has_code_changes"] is False
    assert outcome["summary"]["has_report"] is False
    assert outcome["summary"]["risk_level"] == "low"
    assert outcome["changes"]["stats"]["total"] == 0
    assert outcome["report"]["applicable"] is False


def test_run_outcome_reports_untracked_new_file_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    _commit_initial(repo)
    (repo / "src").mkdir()
    (repo / "src" / "new_tool.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

    thread_id = "outcome-new-file"
    store = get_event_store()
    store.create_session(thread_id, "新增工具文件", str(repo), status="completed")
    store.update_session(
        thread_id,
        str(repo),
        execution_plan={
            "strategy": "feature_delivery",
            "stages": [{"id": "implement", "status": "completed"}],
            "tasks": [{"id": "stage-01-implement", "status": "completed"}],
        },
        team=[{"name": "Lead", "role": "lead"}, {"name": "Coder", "role": "coder"}],
    )
    store.append_event(
        thread_id,
        "assistant_message",
        content="已新增工具文件。",
        agent="lead",
        workspace_dir=str(repo),
    )

    outcome = build_run_outcome(thread_id, str(repo))

    assert outcome["changes"]["stats"]["created"] == 1
    assert outcome["changes"]["files"][0]["path"] == "src/new_tool.py"
    assert "new file mode 100644" in outcome["changes"]["diff"]
    assert "+def hello():" in outcome["changes"]["diff"]
    assert outcome["summary"]["has_code_changes"] is True


def test_run_outcome_change_stats_cover_deleted_renamed_and_binary(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    (repo / "deleted.py").write_text("print('bye')\n", encoding="utf-8")
    (repo / "old_name.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)

    (repo / "deleted.py").unlink()
    subprocess.run(["git", "mv", "old_name.py", "new_name.py"], cwd=repo, capture_output=True)
    (repo / "image.bin").write_bytes(b"\x00\x01nanoCursor")

    thread_id = "outcome-change-stats"
    store = get_event_store()
    store.create_session(thread_id, "检查变更统计", str(repo), status="completed")
    store.update_session(
        thread_id,
        str(repo),
        execution_plan={"strategy": "feature_delivery", "stages": [], "tasks": []},
        team=[{"name": "Lead", "role": "lead"}],
    )

    outcome = build_run_outcome(thread_id, str(repo))

    assert outcome["changes"]["stats"]["created"] == 1
    assert outcome["changes"]["stats"]["deleted"] == 1
    assert outcome["changes"]["stats"]["renamed"] == 1
    assert outcome["changes"]["stats"]["total"] == 3
    assert "Binary files /dev/null and b/image.bin differ" in outcome["changes"]["diff"]


def test_run_outcome_keeps_analysis_only_read_only_shape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = get_event_store()
    thread_id = "outcome-analysis"
    store.create_session(thread_id, "只分析 README，不修改文件", str(workspace), status="completed")
    store.update_session(
        thread_id,
        str(workspace),
        execution_plan={
            "strategy": "analysis_only",
            "stages": [
                {"id": "intake", "title": "接收需求", "status": "completed"},
                {"id": "plan", "title": "分析计划", "status": "completed"},
            ],
            "tasks": [
                {"id": "stage-01-intake", "status": "completed"},
                {"id": "stage-02-plan", "status": "completed"},
            ],
        },
        team=[{"name": "Lead", "role": "lead"}, {"name": "Planner", "role": "planner"}],
        runtime_team_source="runtime_recommended",
    )
    store.append_event(
        thread_id,
        "assistant_message",
        content="README 当前缺少快速开始说明。",
        agent="lead",
        workspace_dir=str(workspace),
    )

    outcome = build_run_outcome(thread_id, str(workspace))

    assert outcome["strategy"] == "analysis_only"
    assert [stage["id"] for stage in outcome["stages"]] == ["intake", "plan"]
    assert outcome["changes"]["files"] == []
    assert outcome["summary"]["final_message"] == "README 当前缺少快速开始说明。"
    assert outcome["summary"]["risk_level"] == "low"
    assert outcome["team"]["runtime_source"] == "runtime_recommended"


def test_run_outcome_api_route_returns_outcome(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "outcome-api"
    get_event_store().create_session(thread_id, "检查 outcome", str(workspace), status="completed")

    client = TestClient(api_server.app)
    response = client.get(f"/api/runs/{thread_id}/outcome")

    assert response.status_code == 200
    assert response.json()["thread_id"] == thread_id
    assert response.json()["workspace_dir"] == str(workspace.resolve())
