import json

import pytest

from src.api.services.agenthub_state import add_team_member, infer_task_capabilities, list_task_items, list_team_members
from src.api.services.artifact_service import build_artifact_center
from src.api.services.benchmark_service import emit_benchmark_run, get_benchmark, list_benchmarks, write_benchmark_artifacts
from src.api.services.capability_service import build_capability_hub, import_workspace_skill, recommend_capabilities
from src.api.services.mcp_status_service import update_mcp_status
from src.api.services.conversation_service import (
    create_conversation,
    finalize_conversation_run,
    get_conversation,
    link_run_to_conversation,
    list_conversations,
    refresh_conversation_recommendation,
    update_conversation_team,
)
from src.api.services.demo_run import emit_demo_run, write_demo_artifacts
from src.api.services.preference_service import add_preference_memory, build_memory_profile
from src.api.services.quality_service import build_quality_gate
from src.api.services.recovery_service import build_recovery_center, rollback_from_backup
from src.api.services.report_service import build_delivery_report
from src.api.services.score_service import build_delivery_score
from src.api.services.traceability_service import build_requirement_traceability
from src.api.services.event_store import EventStore
from src.api.services.tool_events import capability_trace_for_tool, derive_agenthub_events
from src.api.services.workspace_registry_service import get_workspace_identity, list_recent_projects, open_project
from src.api.services.workspace_service import build_workspace_health, build_workspace_overview
from src.api.services.workspace_settings_service import get_workspace_settings, save_workspace_settings
from src.agent.context_pack import ContextPack
from src.agent.strategy.planner import select_strategy
from src.agent.strategy.tool_policy import ToolPolicy
from src.api.services.checkpoint_service import create_checkpoint, list_checkpoints, restore_checkpoint
from src.api.services.eval_service import build_aggregate_metrics, list_evals, run_eval
from src.api.services.git_sandbox_service import commit_branch, discard_branch, git_branch_status, prepare_git_branch
from src.api.services.recovery_service import _action_risk_level
from src.runtime.run_events import enrich_event
from src.runtime.run_manager import RunManager
from src.runtime.run_state import RunStateMachine, RunStatus
from src.api.services.mcp_service import (
    install_mcp_server_preset,
    list_mcp_server_presets,
    list_mcp_servers,
    upsert_mcp_server_config,
    validate_mcp_config,
)
from src.api.services.capability_usage_service import build_capability_usage
from src.api.services.failure_classifier_service import classify_failure
from src.api.services.recovery_action_service import execute_recovery_action
from src.api.services.skill_service import delete_workspace_skill, get_skill_detail, update_workspace_skill


def test_list_task_items_normalizes_workspace_tasks(tmp_path):
    workspace = tmp_path / "workspace"
    tasks_dir = workspace / ".tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "task_1.json").write_text(
        json.dumps(
            {
                "id": "1",
                "subject": "Build UI",
                "description": "Create the workbench",
                "status": "working",
                "owner": "Coder",
                "blocked_by": ["0"],
                "created_at": 2,
            }
        ),
        encoding="utf-8",
    )

    tasks = list_task_items(str(workspace))

    assert tasks == [
        {
            "id": "1",
            "title": "Build UI",
            "description": "Create the workbench",
            "status": "in_progress",
            "owner": "Coder",
            "capabilities": ["tool.file_ops", "tool.project_index", "skill.frontend-polish"],
            "dependencies": ["0"],
            "result": "",
            "created_at": 2,
            "updated_at": None,
        }
    ]


def test_infer_task_capabilities_maps_ui_and_verification_tasks():
    capabilities = infer_task_capabilities(
        "实现前端界面并补充测试验证",
        "调整布局后运行质量复核",
        "Tester",
    )

    assert "tool.file_ops" in capabilities
    assert "skill.frontend-polish" in capabilities
    assert "skill.delivery-review" in capabilities
    assert "tool.recovery" in capabilities


def test_list_team_members_uses_default_team_when_config_missing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    members = list_team_members(str(workspace))

    assert [member["role"] for member in members] == ["lead", "planner", "coder", "tester"]
    assert all(member["source"] == "default" for member in members)
    assert members[0]["goal"]
    assert "tools" in members[0]


def test_add_team_member_persists_custom_agent_card(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    member = add_team_member(
        name="Reviewer",
        role="Code Reviewer",
        goal="Review code changes before delivery.",
        tools=["diff", "quality"],
        capabilities=["tool.project_index", "skill.delivery-review"],
        workspace_dir=str(workspace),
    )
    members = list_team_members(str(workspace))

    assert member["name"] == "Reviewer"
    assert member["role"] == "code_reviewer"
    assert member["source"] == "workspace"
    assert member["tools"] == ["diff", "quality"]
    assert member["capabilities"] == ["tool.project_index", "skill.delivery-review"]
    assert [item["name"] for item in members][-1] == "Reviewer"
    assert members[-1]["capabilities"] == ["tool.project_index", "skill.delivery-review"]
    assert (workspace / ".team" / "config.json").exists()


def test_add_team_member_rejects_duplicate_name(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    add_team_member("Reviewer", "reviewer", workspace_dir=str(workspace))

    try:
        add_team_member("reviewer", "reviewer", workspace_dir=str(workspace))
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate agent name should fail")


def test_build_capability_hub_lists_tools_mcp_and_workspace_skills(tmp_path):
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Review Skill\n\nCheck delivery quality.", encoding="utf-8")
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "npx"}}}),
        encoding="utf-8",
    )

    hub = build_capability_hub(str(workspace))

    assert hub["summary"]["total"] >= 3
    assert hub["summary"]["configured"] >= 2
    assert [group["id"] for group in hub["groups"]] == ["tool", "mcp", "skill"]
    assert any(item["id"] == "mcp.github" and item["status"] == "configured" for item in hub["capabilities"])
    github = next(item for item in hub["capabilities"] if item["id"] == "mcp.github")
    workspace_skill = next(item for item in hub["capabilities"] if item["id"] == "skill.review")
    builtin_skill = next(item for item in hub["capabilities"] if item["id"] == "skill.delivery-review")
    assert github["setup_source"] == ".mcp.json"
    assert workspace_skill["name"] == "Review Skill"
    assert "项目专属工作流" in workspace_skill["use_cases"]
    assert "outputs" in builtin_skill
    assert "risks" in builtin_skill


def test_import_workspace_skill_writes_skill_markdown(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    imported = import_workspace_skill(
        "API Review",
        description="检查接口兼容性",
        content="检查 OpenAPI、错误码和响应结构。",
        workspace_dir=str(workspace),
    )
    hub = build_capability_hub(str(workspace))

    skill_path = workspace / ".nanocursor" / "skills" / "api-review" / "SKILL.md"
    assert imported["id"] == "skill.api-review"
    assert skill_path.exists()
    assert "检查 OpenAPI" in skill_path.read_text(encoding="utf-8")
    assert any(item["id"] == "skill.api-review" and item["status"] == "configured" for item in hub["capabilities"])


def test_workspace_overview_aggregates_project_state(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    (workspace / "test_app.py").write_text("def test_hello():\n    assert True\n", encoding="utf-8")
    import_workspace_skill("API Review", "Review API contracts.", workspace_dir=str(workspace))
    create_conversation("帮我复核 API", str(workspace))
    store = EventStore()
    store.create_session("run-1", "Prompt", str(workspace), status="failed")
    store.append_event("run-1", "error", content="boom", workspace_dir=str(workspace))
    snapshot_dir = workspace / ".snapshots" / "snap-1"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "metadata.json").write_text("{}", encoding="utf-8")

    overview = build_workspace_overview(str(workspace))

    assert overview["workspace_dir"] == str(workspace.resolve())
    assert overview["summary"]["conversation_count"] == 1
    assert overview["summary"]["recent_run_count"] == 1
    assert overview["summary"]["failed_run_count"] == 1
    assert overview["summary"]["custom_skill_count"] == 1
    assert overview["summary"]["recovery_point_count"] == 1
    assert overview["project_index"]["total_files"] >= 2
    assert overview["recent_conversations"][0]["prompt"] == "帮我复核 API"


def test_recommend_capabilities_matches_frontend_quality_request(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    recommendation = recommend_capabilities("帮我打磨前端界面并补充测试验证", str(workspace))

    capability_ids = [item["id"] for item in recommendation["capabilities"]]
    assert "Designer" in recommendation["agents"]
    assert "Tester" in recommendation["agents"]
    assert "skill.frontend-polish" in capability_ids
    assert "skill.delivery-review" in capability_ids
    assert recommendation["summary"]["capability_count"] == len(recommendation["capabilities"])


def test_recommend_capabilities_builds_usable_mcp_plan_from_cached_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".nanocursor").mkdir()
    (workspace / ".nanocursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "python", "args": ["server.py"]}}}),
        encoding="utf-8",
    )
    update_mcp_status(
        "mcp.github",
        {
            "status": "ready",
            "tools_cache": {
                "cached_at": 1000.0,
                "config_hash": "abc",
                "tools": [
                    {"name": "list_issues", "description": "List repository issues"},
                    {"name": "get_pr", "description": "Read pull request"},
                ],
            },
        },
        str(workspace),
    )

    recommendation = recommend_capabilities("帮我查看 GitHub issue 和 PR 状态", str(workspace))
    plan = next(item for item in recommendation["mcp_plan"] if item["server_id"] == "mcp.github")

    assert plan["configured"] is True
    assert plan["usable"] is True
    assert plan["tool_count"] == 2
    assert plan["tools"][0]["name"] == "list_issues"
    assert recommendation["summary"]["usable_mcp_count"] == 1


def test_create_conversation_persists_recommended_team(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    conversation = create_conversation("帮我打磨前端界面并补充测试", str(workspace))
    loaded = get_conversation(conversation["conversation_id"], str(workspace))
    conversations = list_conversations(str(workspace))

    assert conversation["agent_loop_policy"] == "run_per_message"
    assert conversation["workspace_dir"] == str(workspace.resolve())
    assert loaded is not None
    assert loaded["team"]["source"] == "recommended"
    assert any(member["name"] == "Designer" for member in loaded["team"]["members"])
    assert conversations[0]["conversation_id"] == conversation["conversation_id"]
    assert (workspace / ".nanocursor" / "conversations" / conversation["conversation_id"] / "team.json").exists()


def test_update_conversation_team_replaces_members(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversation = create_conversation("实现一个后端接口", str(workspace))

    team = update_conversation_team(
        conversation["conversation_id"],
        [
            {
                "name": "Architect",
                "role": "architect",
                "goal": "设计后端上下文边界",
                "tools": ["plan"],
                "capabilities": ["tool.project_index"],
            }
        ],
        str(workspace),
    )

    assert team["source"] == "user"
    assert [member["name"] for member in team["members"]] == ["Architect"]
    assert get_conversation(conversation["conversation_id"], str(workspace))["team"]["members"][0]["role"] == "architect"


def test_refresh_recommendation_and_link_run_to_conversation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversation = create_conversation("初始需求", str(workspace))

    result = refresh_conversation_recommendation(
        conversation["conversation_id"],
        "修复前端 bug 并做质量复核",
        str(workspace),
    )
    linked = link_run_to_conversation(conversation["conversation_id"], "run-123", str(workspace))

    assert result["team"]["source"] == "recommended"
    assert "run-123" in linked["run_ids"]
    assert linked["current_thread_id"] == "run-123"
    assert linked["status"] == "running"
    assert linked["run_records"][0]["thread_id"] == "run-123"
    assert linked["run_records"][0]["status"] == "running"


def test_finalize_conversation_run_updates_record_and_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversation = create_conversation("实现会话状态闭环", str(workspace))

    linked = link_run_to_conversation(
        conversation["conversation_id"],
        "run-456",
        str(workspace),
        prompt="实现会话状态闭环",
        team=[{"name": "Tester", "role": "tester"}],
    )
    finalized = finalize_conversation_run(
        conversation["conversation_id"],
        "run-456",
        "completed",
        str(workspace),
        summary="会话状态已回写。",
    )

    assert linked["run_count"] == 1
    assert finalized["status"] == "completed"
    assert finalized["last_run_status"] == "completed"
    assert finalized["last_run_summary"] == "会话状态已回写。"
    assert finalized["latest_run"]["thread_id"] == "run-456"
    assert finalized["latest_run"]["status"] == "completed"
    assert finalized["latest_run"]["summary"] == "会话状态已回写。"
    assert finalized["latest_run"]["team"] == [{"name": "Tester", "role": "tester"}]


def test_capability_trace_for_tool_maps_tool_to_agent_capability():
    trace = capability_trace_for_tool("write_file")

    assert trace["agent"] == "Coder"
    assert trace["capability_id"] == "tool.file_ops"
    assert trace["capability_name"] == "文件读写"
    assert trace["tool"] == "write_file"


def test_build_delivery_report_generates_from_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-1", "Build a todo app", str(workspace), status="completed")
    store.append_event(
        "run-1",
        "assistant_message",
        content="Implemented the requested todo app.",
        workspace_dir=str(workspace),
    )
    store.append_event(
        "run-1",
        "tool_call_finished",
        title="write_file",
        payload={"tool": "write_file"},
        workspace_dir=str(workspace),
    )

    report = build_delivery_report("run-1", str(workspace))

    assert report["source"] == "generated"
    assert "Implemented the requested todo app." in report["markdown"]
    assert "Build a todo app" in report["markdown"]


def test_build_delivery_report_includes_execution_stages(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-stages", "Build a todo app", str(workspace), status="completed")
    store.update_session(
        "run-stages",
        str(workspace),
        execution_plan={
            "stages": [
                {
                    "id": "implement",
                    "title": "代码实现",
                    "owner": "Coder",
                    "status": "completed",
                    "tool_evidence": [{"tool": "write_file"}],
                },
                {
                    "id": "verify",
                    "title": "验证复核",
                    "owner": "Tester",
                    "status": "completed",
                    "tool_evidence": [{"tool": "bash"}],
                },
            ]
        },
    )

    report = build_delivery_report("run-stages", str(workspace))

    assert "## Execution Stages" in report["markdown"]
    assert "代码实现" in report["markdown"]
    assert "write_file" in report["markdown"]


def test_build_delivery_report_handles_missing_capability_usage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    report = build_delivery_report("missing-run", str(workspace))

    assert report["source"] == "generated"
    assert report["capabilities_used"] == []
    assert "Capability usage data is not available" in report["markdown"]


def test_derive_task_created_event(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    events = derive_agenthub_events(
        "task_create",
        {"subject": "Build UI", "description": "Create workbench", "blocked_by": ["0"]},
        "Created task 123: Build UI",
        str(workspace),
    )

    assert events[0]["event_type"] == "task_created"
    assert events[0]["payload"]["task_id"] == "123"
    assert events[0]["payload"]["task"]["title"] == "Build UI"
    assert events[0]["payload"]["task"]["dependencies"] == ["0"]


def test_derive_task_updated_event():
    events = derive_agenthub_events(
        "task_update",
        {"task_id": "123", "status": "completed"},
        "Updated task 123 to completed",
        ".",
    )

    assert events == [
        {
            "event_type": "task_updated",
            "title": "更新任务：123",
            "content": "任务状态变更为 completed",
            "agent": "lead",
            "payload": {"task_id": "123", "status": "completed"},
        }
    ]


def test_derive_team_updated_event_uses_default_team(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    events = derive_agenthub_events(
        "spawn_teammate",
        {"name": "Coder", "role": "coder", "prompt": "Implement feature"},
        "Spawned teammate 'Coder'",
        str(workspace),
    )

    assert events[0]["event_type"] == "team_updated"
    assert [member["role"] for member in events[0]["payload"]["members"]] == [
        "lead",
        "planner",
        "coder",
        "tester",
    ]


def test_derive_file_events_for_write_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    events = derive_agenthub_events(
        "write_file",
        {"path": "app.py", "content": "print('hi')"},
        "Wrote 11 bytes",
        str(workspace),
        thread_id="run-1",
    )

    assert [event["event_type"] for event in events] == ["file_changed", "diff_updated"]
    assert events[0]["payload"]["path"] == "app.py"
    assert events[0]["payload"]["change_type"] == "modified"
    assert "changed_files" in events[1]["payload"]


def test_derive_file_events_skips_failed_tool_call(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    events = derive_agenthub_events(
        "edit_file",
        {"path": "app.py", "old_text": "x", "new_text": "y"},
        "Error: Text not found",
        str(workspace),
        thread_id="run-1",
    )

    assert events == []


def test_write_demo_artifacts_creates_files_and_report(tmp_path):
    workspace = tmp_path / "workspace"

    artifacts = write_demo_artifacts("demo-run", str(workspace))

    assert (workspace / "demo-todo" / "index.html").exists()
    assert (workspace / ".tasks" / "task_demo-001.json").exists()
    assert (workspace / ".team" / "config.json").exists()
    assert (workspace / ".nanocursor" / "runs" / "demo-run" / "report.md").exists()
    assert (workspace / ".nanocursor" / "runs" / "demo-run" / "requirements.json").exists()
    assert artifacts["changed_files"][0]["change_type"] == "created"


def test_emit_demo_run_records_complete_event_stream(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("demo-run", "Demo prompt", str(workspace), status="running")

    emit_demo_run("demo-run", str(workspace), store, delay=0)

    events = store.list_events("demo-run", str(workspace))

    assert events[0].type == "assistant_message"
    assert any(event.type == "approval_requested" for event in events)
    assert any(event.type == "task_created" for event in events)
    assert any(event.type == "file_changed" for event in events)
    assert any(event.type == "diff_updated" for event in events)
    assert events[-1].type == "done"
    assert store.get_session("demo-run", str(workspace))["status"] == "completed"


def test_quality_gate_passes_for_demo_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("demo-run", "Demo prompt", str(workspace), status="running")
    emit_demo_run("demo-run", str(workspace), store, delay=0)

    quality = build_quality_gate("demo-run", str(workspace))

    assert quality["status"] == "passed"
    assert quality["failed_count"] == 0
    assert {check["id"] for check in quality["checks"] if check["status"] == "passed"} >= {
        "session_recorded",
        "run_completed",
        "tasks_created",
        "file_changes",
        "diff_available",
        "tests_finished",
        "report_ready",
    }


def test_quality_gate_fails_for_error_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("bad-run", "Prompt", str(workspace), status="failed")
    store.append_event("bad-run", "run_started", workspace_dir=str(workspace))
    store.append_event("bad-run", "error", content="boom", workspace_dir=str(workspace))

    quality = build_quality_gate("bad-run", str(workspace))

    assert quality["status"] == "failed"
    failed_ids = {check["id"] for check in quality["checks"] if check["status"] == "failed"}
    assert "run_completed" in failed_ids
    assert "no_runtime_errors" in failed_ids
    assert "file_changes" in failed_ids


def test_quality_gate_warns_when_recommended_test_missing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("almost-run", "Prompt", str(workspace), status="completed")
    store.append_event("almost-run", "plan_created", workspace_dir=str(workspace))
    store.append_event(
        "almost-run",
        "task_created",
        payload={"task_id": "t1"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        "almost-run",
        "task_updated",
        payload={"task_id": "t1", "status": "completed"},
        workspace_dir=str(workspace),
    )
    store.append_event("almost-run", "file_changed", workspace_dir=str(workspace))
    store.append_event("almost-run", "diff_updated", workspace_dir=str(workspace))
    store.append_event("almost-run", "report_ready", workspace_dir=str(workspace))
    store.append_event(
        "almost-run",
        "done",
        payload={"status": "completed"},
        workspace_dir=str(workspace),
    )
    run_dir = store.run_dir("almost-run", str(workspace))
    (run_dir / "diff.patch").write_text("diff", encoding="utf-8")
    (run_dir / "report.md").write_text("# report", encoding="utf-8")

    quality = build_quality_gate("almost-run", str(workspace))

    assert quality["status"] == "warning"
    warning_ids = {check["id"] for check in quality["checks"] if check["status"] == "warning"}
    assert "tests_finished" in warning_ids


def test_quality_gate_checks_execution_stage_lifecycle(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("stage-run", "Prompt", str(workspace), status="completed")
    store.update_session(
        "stage-run",
        str(workspace),
        execution_plan={
            "stages": [
                {
                    "id": "implement",
                    "title": "代码实现",
                    "owner": "Coder",
                    "status": "completed",
                    "tool_evidence": [{"tool": "write_file"}],
                },
                {
                    "id": "verify",
                    "title": "验证复核",
                    "owner": "Tester",
                    "status": "pending",
                    "required": True,
                },
            ]
        },
    )
    store.append_event("stage-run", "plan_created", workspace_dir=str(workspace))
    store.append_event("stage-run", "task_created", payload={"task_id": "t1"}, workspace_dir=str(workspace))
    store.append_event(
        "stage-run",
        "task_updated",
        payload={"task_id": "t1", "status": "completed"},
        workspace_dir=str(workspace),
    )
    store.append_event("stage-run", "file_changed", workspace_dir=str(workspace))
    store.append_event("stage-run", "diff_updated", workspace_dir=str(workspace))
    store.append_event("stage-run", "test_finished", workspace_dir=str(workspace))
    store.append_event("stage-run", "report_ready", workspace_dir=str(workspace))
    store.append_event("stage-run", "done", payload={"status": "completed"}, workspace_dir=str(workspace))
    run_dir = store.run_dir("stage-run", str(workspace))
    (run_dir / "diff.patch").write_text("diff", encoding="utf-8")
    (run_dir / "report.md").write_text("# report", encoding="utf-8")

    quality = build_quality_gate("stage-run", str(workspace))
    failed_ids = {check["id"] for check in quality["checks"] if check["status"] == "failed"}

    assert quality["status"] == "failed"
    assert "execution_stages_terminal" in failed_ids
    assert "required_stages_completed" in failed_ids


def test_delivery_score_is_excellent_for_demo_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("demo-run", "Demo prompt", str(workspace), status="running")
    emit_demo_run("demo-run", str(workspace), store, delay=0)

    score = build_delivery_score("demo-run", str(workspace))

    assert score["score"] == 100
    assert score["level"] == "excellent"
    assert score["quality_status"] == "passed"
    assert score["reasons"] == []


def test_delivery_score_caps_warning_quality(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("almost-run", "Prompt", str(workspace), status="completed")
    store.append_event("almost-run", "plan_created", workspace_dir=str(workspace))
    store.append_event(
        "almost-run",
        "task_created",
        payload={"task_id": "t1"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        "almost-run",
        "task_updated",
        payload={"task_id": "t1", "status": "completed"},
        workspace_dir=str(workspace),
    )
    store.append_event("almost-run", "file_changed", workspace_dir=str(workspace))
    store.append_event("almost-run", "diff_updated", workspace_dir=str(workspace))
    store.append_event("almost-run", "report_ready", workspace_dir=str(workspace))
    store.append_event(
        "almost-run",
        "done",
        payload={"status": "completed"},
        workspace_dir=str(workspace),
    )
    run_dir = store.run_dir("almost-run", str(workspace))
    (run_dir / "diff.patch").write_text("diff", encoding="utf-8")
    (run_dir / "report.md").write_text("# report", encoding="utf-8")

    score = build_delivery_score("almost-run", str(workspace))

    assert score["score"] == 84
    assert score["level"] == "good"
    assert score["quality_status"] == "warning"
    assert {reason["id"] for reason in score["reasons"]} == {"tests_finished"}


def test_delivery_score_caps_failed_quality(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("bad-run", "Prompt", str(workspace), status="failed")
    store.append_event("bad-run", "run_started", workspace_dir=str(workspace))
    store.append_event("bad-run", "error", content="boom", workspace_dir=str(workspace))

    score = build_delivery_score("bad-run", str(workspace))

    assert score["score"] < 60
    assert score["level"] == "failed"
    assert score["quality_status"] == "failed"
    reason_ids = {reason["id"] for reason in score["reasons"]}
    assert "run_completed" in reason_ids
    assert "no_runtime_errors" in reason_ids
    assert "file_changes" in reason_ids


def test_requirement_traceability_loads_demo_artifact(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_demo_artifacts("demo-run", str(workspace))

    traceability = build_requirement_traceability("demo-run", str(workspace))

    assert traceability["source"] == "run_artifact"
    assert traceability["total_count"] == 5
    assert traceability["covered_count"] == 5
    assert traceability["coverage_rate"] == 1.0
    first_requirement = traceability["requirements"][0]
    assert first_requirement["id"] == "REQ-001"
    assert "demo-002" in first_requirement["tasks"]
    assert "demo-todo/app.js" in first_requirement["files"]


def test_requirement_traceability_generates_fallback_from_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("run-1", "Build a small calculator", str(workspace), status="completed")
    store.append_event(
        "run-1",
        "task_created",
        title="Create calculator UI",
        payload={"task_id": "t1", "task": {"title": "Create calculator UI"}},
        workspace_dir=str(workspace),
    )
    store.append_event(
        "run-1",
        "file_changed",
        payload={"path": "calculator/index.html"},
        workspace_dir=str(workspace),
    )
    store.append_event(
        "run-1",
        "test_finished",
        payload={"checks": ["add numbers"]},
        workspace_dir=str(workspace),
    )
    run_dir = store.run_dir("run-1", str(workspace))
    (run_dir / "changed_files.json").write_text(
        json.dumps([{"path": "calculator/index.html", "change_type": "created"}]),
        encoding="utf-8",
    )
    (run_dir / "diff.patch").write_text("diff", encoding="utf-8")

    traceability = build_requirement_traceability("run-1", str(workspace))

    assert traceability["source"] == "generated"
    assert traceability["covered_count"] == 1
    requirement = traceability["requirements"][0]
    assert requirement["status"] == "covered"
    assert requirement["title"] == "Build a small calculator"
    assert requirement["tasks"] == ["t1: Create calculator UI"]
    assert requirement["files"] == ["calculator/index.html"]
    assert requirement["tests"] == ["add numbers"]


def test_artifact_center_collects_demo_delivery_artifacts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("demo-run", "Demo prompt", str(workspace), status="running")
    emit_demo_run("demo-run", str(workspace), store, delay=0)

    center = build_artifact_center("demo-run", str(workspace))

    assert center["status"] == "ready"
    assert center["summary"]["artifact_count"] == 9
    assert center["summary"]["score"] == 100
    assert center["summary"]["coverage_rate"] == 1.0
    artifacts = {item["id"]: item for item in center["artifacts"]}
    assert artifacts["requirements"]["status"] == "ready"
    assert artifacts["requirements"]["count"] == 5
    assert artifacts["tasks"]["summary"] == "4 / 4 个任务已完成"
    assert artifacts["changed_files"]["count"] == 3
    assert artifacts["tests"]["count"] == 1
    assert artifacts["quality"]["payload"]["status"] == "passed"


def test_artifact_center_marks_failed_run_incomplete(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("bad-run", "Prompt", str(workspace), status="failed")
    store.append_event("bad-run", "run_started", workspace_dir=str(workspace))
    store.append_event("bad-run", "error", content="boom", workspace_dir=str(workspace))

    center = build_artifact_center("bad-run", str(workspace))

    assert center["status"] == "incomplete"
    artifacts = {item["id"]: item for item in center["artifacts"]}
    assert artifacts["changed_files"]["status"] == "missing"
    assert artifacts["diff_patch"]["status"] == "missing"
    assert artifacts["risks"]["status"] == "warning"
    assert artifacts["score"]["payload"]["level"] == "failed"


def test_list_benchmarks_returns_three_fixed_tasks(tmp_path):
    workspace = tmp_path / "workspace"

    benchmarks = list_benchmarks(str(workspace))

    assert [item["id"] for item in benchmarks] == ["todo-web-app", "python-utils", "bugfix-cart"]
    assert all("files" not in item for item in benchmarks)
    assert benchmarks[0]["expected_artifacts"]


def test_write_benchmark_artifacts_creates_delivery_evidence(tmp_path):
    workspace = tmp_path / "workspace"

    artifacts = write_benchmark_artifacts("bench-run", "python-utils", str(workspace))

    run_dir = workspace / ".nanocursor" / "runs" / "bench-run"
    assert (workspace / "benchmarks" / "python-utils" / "string_tools.py").exists()
    assert (run_dir / "changed_files.json").exists()
    assert (run_dir / "diff.patch").exists()
    assert (run_dir / "requirements.json").exists()
    assert (run_dir / "report.md").exists()
    assert artifacts["benchmark"]["id"] == "python-utils"
    assert len(artifacts["requirements"]) >= 3


def test_emit_benchmark_run_records_complete_stream(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("bench-run", "Benchmark prompt", str(workspace), status="running", mode="agenthub_benchmark")

    emit_benchmark_run("bench-run", "bugfix-cart", str(workspace), store, delay=0)

    events = store.list_events("bench-run", str(workspace))
    assert any(event.type == "benchmark_finished" for event in events)
    assert any(event.type == "traceability_ready" for event in events)
    assert events[-1].type == "done"
    assert store.get_session("bench-run", str(workspace))["status"] == "completed"


def test_get_benchmark_rejects_unknown_id():
    try:
        get_benchmark("missing-benchmark")
    except ValueError as exc:
        assert "Unknown benchmark" in str(exc)
    else:
        raise AssertionError("unknown benchmark should fail")


def test_memory_profile_groups_preference_memories(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    add_preference_memory(
        "code_style",
        "Prefer explicit Python type hints and small helper functions.",
        importance=9,
        workspace_dir=str(workspace),
    )
    add_preference_memory(
        "testing",
        "Use pytest for backend behavior checks.",
        importance=7,
        workspace_dir=str(workspace),
    )

    profile = build_memory_profile(str(workspace))

    assert profile["preference_count"] == 2
    assert profile["high_importance_count"] == 2
    buckets = {bucket["id"]: bucket for bucket in profile["buckets"]}
    assert buckets["code_style"]["confidence"] == "high"
    assert buckets["code_style"]["memories"][0]["importance"] == 9
    assert "代码风格" in profile["prompt_context"]
    assert "pytest" in profile["prompt_context"]


def test_memory_profile_infers_bucket_from_existing_memory_content(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    memory_dir = workspace / ".memory" / "user"
    memory_dir.mkdir(parents=True)
    (memory_dir / "ui-pref.md").write_text(
        "\n".join(
            [
                "---",
                "id: ui-pref",
                "category: user",
                "importance: 6",
                "tags: design",
                "created_at: 1",
                "last_accessed_at: 1",
                "access_count: 0",
                "session_id:",
                "---",
                "",
                "UI 偏好是专业、克制、高信息密度的界面。",
            ]
        ),
        encoding="utf-8",
    )

    profile = build_memory_profile(str(workspace))

    buckets = {bucket["id"]: bucket for bucket in profile["buckets"]}
    assert profile["total_memories"] == 1
    assert buckets["ui_style"]["confidence"] == "medium"
    assert buckets["ui_style"]["memories"][0]["id"] == "ui-pref"


def test_add_preference_memory_rejects_unknown_type(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    try:
        add_preference_memory("unknown", "Prefer something", workspace_dir=str(workspace))
    except ValueError as exc:
        assert "Unknown preference type" in str(exc)
    else:
        raise AssertionError("unknown preference type should fail")


def test_recovery_center_collects_snapshots_and_backups(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot_dir = workspace / ".snapshots" / "snap-001"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "metadata.json").write_text(
        json.dumps({"timestamp": "2026-05-14T09:00:00", "reason": "before_run", "active_files": ["app.py"]}),
        encoding="utf-8",
    )
    backups_dir = workspace / ".backups"
    backups_dir.mkdir()
    (backups_dir / "app.py.bak.20260514_090000").write_text("print('old')", encoding="utf-8")

    center = build_recovery_center(workspace_dir=str(workspace))

    assert center["status"] == "safe"
    assert center["summary"]["snapshot_count"] == 1
    assert center["summary"]["backup_count"] == 1
    assert {point["kind"] for point in center["recovery_points"]} == {"snapshot", "backup"}
    assert center["actions"][0]["id"] == "continue-delivery"


def test_recovery_center_reports_failed_run_risks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("bad-run", "Prompt", str(workspace), status="failed")
    store.append_event("bad-run", "error", content="boom", workspace_dir=str(workspace))
    store.append_event(
        "bad-run",
        "tool_call_finished",
        payload={"tool": "bash", "input": {"command": "rm -rf dist"}},
        workspace_dir=str(workspace),
    )

    center = build_recovery_center("bad-run", str(workspace))

    assert center["status"] == "attention"
    assert center["summary"]["high_risk_count"] >= 2
    titles = {risk["title"] for risk in center["risks"]}
    action_ids = {action["id"] for action in center["actions"]}
    assert "Run recorded an error event" in titles
    assert "Potentially dangerous command observed" in titles
    assert "inspect-failure-event" in action_ids
    assert "open-quality-gate" in action_ids


def test_recovery_center_points_to_failed_execution_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("failed-stage-run", "Prompt", str(workspace), status="failed")
    store.update_session(
        "failed-stage-run",
        str(workspace),
        execution_plan={
            "stages": [
                {
                    "id": "implement",
                    "title": "代码实现",
                    "owner": "Coder",
                    "status": "failed",
                    "failure": "Error: edit_file failed",
                    "tool_evidence": [{"tool": "edit_file", "ok": False}],
                },
                {"id": "verify", "title": "验证复核", "owner": "Tester", "status": "skipped"},
            ]
        },
    )

    center = build_recovery_center("failed-stage-run", str(workspace))

    risk = next(item for item in center["risks"] if item["id"] == "stage-implement")
    action_ids = {action["id"] for action in center["actions"]}

    assert risk["title"] == "Execution stage failed"
    assert risk["evidence"]["stage_id"] == "implement"
    assert "inspect-failed-stage" in action_ids


def test_recovery_center_recommends_snapshot_when_failed_run_has_no_recovery_point(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("bad-run-no-point", "Prompt", str(workspace), status="failed")
    store.append_event("bad-run-no-point", "error", content="boom", workspace_dir=str(workspace))

    center = build_recovery_center("bad-run-no-point", str(workspace))

    action_ids = [action["id"] for action in center["actions"]]
    assert "create-recovery-point" in action_ids
    assert center["summary"]["action_count"] == len(center["actions"])


def test_rollback_from_backup_restores_target_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backups_dir = workspace / ".backups"
    backups_dir.mkdir()
    (backups_dir / "app.py.bak.20260514_090000").write_text("print('old')", encoding="utf-8")
    target = workspace / "app.py"
    target.write_text("print('new')", encoding="utf-8")

    result = rollback_from_backup("app.py.bak.20260514_090000", "app.py", str(workspace))

    assert result["restored"] is True
    assert result["target_path"] == "app.py"
    assert target.read_text(encoding="utf-8") == "print('old')"


def test_rollback_from_backup_blocks_path_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backups_dir = workspace / ".backups"
    backups_dir.mkdir()
    (backups_dir / "app.py.bak.20260514_090000").write_text("print('old')", encoding="utf-8")

    try:
        rollback_from_backup("app.py.bak.20260514_090000", "../escape.py", str(workspace))
    except ValueError as exc:
        assert "escapes workspace" in str(exc)
    else:
        raise AssertionError("path escape should fail")


# --- MCP config detail tests ---

def test_list_mcp_servers_parses_config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "node", "args": ["server.js"], "env": {"GITHUB_TOKEN": "xxx"}}}}),
        encoding="utf-8",
    )
    result = list_mcp_servers(str(workspace))

    assert ".mcp.json" in result["config_paths"]
    github = next(s for s in result["servers"] if s["id"] == "mcp.github")
    assert github["status"] == "configured"
    assert github["command"] == "node"
    assert github["args"] == ["server.js"]
    assert "GITHUB_TOKEN" in github["env_keys"]
    assert github["source"] == ".mcp.json"


def test_list_mcp_servers_includes_templates_when_no_config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = list_mcp_servers(str(workspace))

    assert len(result["config_paths"]) == 0
    template_ids = [s["id"] for s in result["servers"] if s["status"] == "planned"]
    assert "mcp.filesystem" in template_ids
    assert "mcp.memory" in template_ids
    assert "mcp.sequential-thinking" in template_ids
    assert "mcp.github" in template_ids
    assert "mcp.figma" in template_ids
    assert "mcp.docs" in template_ids


def test_list_mcp_servers_scans_multiple_config_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".cursor").mkdir(parents=True)
    (workspace / ".cursor" / "mcp.json").write_text(
        json.dumps({"servers": {"figma": {"command": "figma-mcp"}}}),
        encoding="utf-8",
    )
    result = list_mcp_servers(str(workspace))

    assert ".cursor/mcp.json" in result["config_paths"]
    assert str(workspace / ".mcp.json").split("/")[-1] not in [p.split("/")[-1] for p in result["config_paths"]]
    figma = next(s for s in result["servers"] if s["id"] == "mcp.figma")
    assert figma["status"] == "configured"
    assert figma["source"] == ".cursor/mcp.json"


def test_upsert_mcp_server_config_writes_workspace_config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    server = upsert_mcp_server_config(
        "mcp.github",
        "npx",
        ["-y", "@modelcontextprotocol/server-github"],
        ["GITHUB_TOKEN"],
        str(workspace),
    )
    result = list_mcp_servers(str(workspace))

    assert server["id"] == "mcp.github"
    assert ".nanocursor/mcp.json" in result["config_paths"]
    github = next(s for s in result["servers"] if s["id"] == "mcp.github")
    assert github["status"] == "configured"
    assert github["command"] == "npx"
    assert github["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert "GITHUB_TOKEN" in github["env_keys"]


def test_list_mcp_server_presets_marks_installed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    before = list_mcp_server_presets(str(workspace))
    filesystem = next(p for p in before["presets"] if p["id"] == "filesystem")
    assert filesystem["status"] == "available"
    assert str(workspace) in filesystem["args"]

    installed = install_mcp_server_preset("filesystem", str(workspace))
    after = list_mcp_server_presets(str(workspace))
    filesystem_after = next(p for p in after["presets"] if p["id"] == "filesystem")
    config = json.loads((workspace / ".nanocursor" / "mcp.json").read_text(encoding="utf-8"))

    assert installed["server"]["id"] == "mcp.filesystem"
    assert filesystem_after["status"] == "configured"
    assert config["mcpServers"]["filesystem"]["command"] == "npx"
    assert str(workspace) in config["mcpServers"]["filesystem"]["args"]


def test_install_mcp_server_preset_docs_prefers_docs_dir(tmp_path):
    workspace = tmp_path / "workspace"
    docs = workspace / "docs"
    docs.mkdir(parents=True)

    installed = install_mcp_server_preset("docs", str(workspace))

    assert installed["server"]["id"] == "mcp.docs"
    assert str(docs) in installed["server"]["args"]


def test_install_mcp_server_preset_unknown_raises(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError):
        install_mcp_server_preset("not-a-preset", str(workspace))


def test_capability_hub_deduplicates_configured_mcp(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    upsert_mcp_server_config("github", "npx", ["server"], [], str(workspace))

    hub = build_capability_hub(str(workspace))
    github_cards = [item for item in hub["capabilities"] if item["id"] == "mcp.github"]

    assert len(github_cards) == 1
    assert github_cards[0]["status"] == "configured"


def test_validate_mcp_config_checks_command(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"args": ["server.js"]}}}),
        encoding="utf-8",
    )
    result = validate_mcp_config(server_id="mcp.github", workspace_dir=str(workspace))

    checks = result.get("servers", {}).get("mcp.github", {}).get("checks", [])
    command_check = next((c for c in checks if c["id"] == "command_exists"), None)
    assert command_check is not None
    assert command_check["status"] in ("warning", "planned")


# --- Skill detail / CRUD tests ---

def test_get_skill_detail_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = import_workspace_skill("api-review", "API review", "# API Review\n\nCheck API endpoints.", str(workspace))
    skill_id = skill["id"]

    detail = get_skill_detail(skill_id, str(workspace))
    assert detail["id"] == skill_id
    assert detail["name"] == "API Review"
    assert detail["status"] == "configured"
    assert "API Review" in detail["content"]
    assert detail["source"] != "built-in"


def test_get_skill_detail_builtin():
    detail = get_skill_detail("skill.frontend-polish")
    assert detail["id"] == "skill.frontend-polish"
    assert detail["source"] == "built-in"
    assert detail["status"] == "ready"
    assert len(detail["content"]) > 0


def test_update_workspace_skill(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = import_workspace_skill("api-review", "API review", "# Old Content", str(workspace))
    skill_id = skill["id"]

    updated = update_workspace_skill(skill_id, "# New Content\n\nUpdated.", str(workspace))
    assert updated["id"] == skill_id
    assert "New Content" in updated["content"]

    detail = get_skill_detail(skill_id, str(workspace))
    assert "New Content" in detail["content"]


def test_update_builtin_skill_fails():
    try:
        update_workspace_skill("skill.frontend-polish", "# Hacked", workspace_dir=".")
    except ValueError as exc:
        assert "只能查看" in str(exc)
    else:
        raise AssertionError("updating built-in skill should fail")


def test_delete_workspace_skill(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = import_workspace_skill("temp-skill", "temp", "# Temp", str(workspace))
    skill_id = skill["id"]

    result = delete_workspace_skill(skill_id, str(workspace))
    assert result["ok"] is True

    try:
        get_skill_detail(skill_id, str(workspace))
    except ValueError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("deleted skill should not be found")


def test_delete_builtin_skill_fails():
    try:
        delete_workspace_skill("skill.delivery-review", workspace_dir=".")
    except ValueError as exc:
        assert "不能删除" in str(exc)
    else:
        raise AssertionError("deleting built-in skill should fail")


# --- Capability usage tests ---

def test_build_capability_usage_from_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("usage-run", "Test prompt", str(workspace), status="completed")
    store.append_event("usage-run", "tool_call_finished", title="wrote file", payload={
        "tool": "write_file",
        "capability_trace": {"capability_id": "tool.file_ops", "capability_name": "文件读写", "kind": "tool", "agent": "Coder", "tool": "write_file"},
        "stage_id": "implement",
    }, workspace_dir=str(workspace))
    store.append_event("usage-run", "tool_call_finished", title="ran bash", payload={
        "tool": "bash",
        "capability_trace": {"capability_id": "skill.delivery-review", "capability_name": "交付复核", "kind": "skill", "agent": "Tester", "tool": "bash"},
        "stage_id": "verify",
    }, workspace_dir=str(workspace))
    store.append_event("usage-run", "tool_call_finished", title="searched code", payload={
        "tool": "search_codebase",
        "capability_trace": {"capability_id": "tool.project_index", "capability_name": "项目索引", "kind": "tool", "agent": "Planner", "tool": "search_codebase"},
        "stage_id": "plan",
    }, workspace_dir=str(workspace))

    usage = build_capability_usage("usage-run", str(workspace))
    assert usage["thread_id"] == "usage-run"
    assert usage["summary"]["used_count"] == 3
    assert usage["summary"]["tool_count"] == 2
    assert usage["summary"]["skill_count"] == 1

    caps_by_id = {c["id"]: c for c in usage["capabilities"]}
    assert caps_by_id["tool.file_ops"]["status"] == "used"
    assert len(caps_by_id["tool.file_ops"]["evidence"]) == 1
    assert caps_by_id["skill.delivery-review"]["status"] == "used"
    assert caps_by_id["tool.project_index"]["status"] == "used"


def test_build_capability_usage_includes_planned(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    session = store.create_session("planned-run", "Test", str(workspace), status="running")
    store.update_session("planned-run", str(workspace), execution_plan={
        "stages": [
            {"id": "intake", "title": "接收需求", "capabilities": ["tool.memory"]},
            {"id": "plan", "title": "规划", "capabilities": ["tool.project_index"]},
            {"id": "implement", "title": "实现", "capabilities": ["tool.file_ops", "skill.frontend-polish"]},
        ]
    })

    usage = build_capability_usage("planned-run", str(workspace))
    assert usage["thread_id"] == "planned-run"
    caps_by_id = {c["id"]: c for c in usage["capabilities"]}
    assert "tool.memory" in caps_by_id
    assert caps_by_id["tool.memory"]["status"] == "planned"
    assert "skill.frontend-polish" in caps_by_id
    assert caps_by_id["skill.frontend-polish"]["status"] == "planned"


def test_build_capability_usage_nonexistent_run():
    try:
        build_capability_usage("nonexistent-run-id", workspace_dir=".")
    except ValueError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("nonexistent run should raise ValueError")


# --- Failure classifier tests ---

def test_classify_failure_test():
    result = classify_failure("FAILED: test_app.py::test_home - AssertionError: assert 1 == 2")
    assert result["category"] == "test_failure"
    assert result["confidence"] == "high"


def test_classify_failure_syntax():
    result = classify_failure("SyntaxError: invalid syntax at line 42")
    assert result["category"] == "syntax_error"
    assert result["confidence"] == "high"


def test_classify_failure_permission():
    result = classify_failure("Permission denied: cannot write to /etc/config")
    assert result["category"] == "permission_denied"
    assert result["confidence"] == "high"


def test_classify_failure_unknown():
    result = classify_failure("Something went wrong but we don't know what")
    assert result["category"] == "unknown"
    assert result["confidence"] == "low"


# --- Recovery action execution tests ---

def test_execute_inspect_failed_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("test-run", "Test", str(workspace), status="failed")
    store.update_session("test-run", str(workspace), execution_plan={
        "stages": [
            {"id": "implement", "title": "实现", "status": "failed", "failure": "tool error", "tool_evidence": []},
            {"id": "verify", "title": "验证", "status": "skipped"},
        ]
    })

    result = execute_recovery_action("test-run", "inspect-failed-stage", "", False, str(workspace))
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert "实现" in result["message"]


def test_execute_restore_backup_requires_confirmed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("test-run", "Test", str(workspace))

    try:
        execute_recovery_action("test-run", "restore-backup", "file.bak", False, str(workspace))
    except ValueError as exc:
        assert "confirmed" in str(exc)
    else:
        raise AssertionError("restore-backup without confirmed should fail")


def test_execute_rerun_tests(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("test-run", "Test", str(workspace))

    result = execute_recovery_action("test-run", "rerun-tests", "", False, str(workspace))
    assert result["ok"] is True
    assert result["status"] == "completed"


# --- E1 Workspace registry / settings / health tests ---

def test_open_project_writes_workspace_json(tmp_path):
    proj = tmp_path / "myproject"
    proj.mkdir()
    result = open_project(str(proj))
    assert result["path"] == str(proj)
    assert result["workspace_id"].startswith("ws_")
    assert result["schema_version"] == 1
    assert (proj / ".nanocursor" / "workspace.json").exists()
    identity = json.loads((proj / ".nanocursor" / "workspace.json").read_text(encoding="utf-8"))
    assert identity["trusted"] is False
    assert identity["schema_version"] == 1


def test_open_project_updates_recent(tmp_path):
    proj = tmp_path / "myproject"
    proj.mkdir()
    open_project(str(proj))
    recent = list_recent_projects()
    assert any(r["path"] == str(proj) for r in recent)


def test_open_project_preserves_workspace_id_when_directory_moves(tmp_path):
    old_proj = tmp_path / "old"
    new_proj = tmp_path / "new"
    old_proj.mkdir()
    new_proj.mkdir()

    identity = open_project(str(old_proj))
    nc_dir = new_proj / ".nanocursor"
    nc_dir.mkdir()
    (nc_dir / "workspace.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    moved = open_project(str(new_proj))

    assert moved["workspace_id"] == identity["workspace_id"]
    assert moved["path"] == str(new_proj)
    assert moved["previous_path"] == str(old_proj)


def test_open_project_rejects_relative_path():
    try:
        open_project("relative/path")
    except ValueError as exc:
        assert "绝对路径" in str(exc)
    else:
        raise AssertionError("relative path should fail")


def test_workspace_health(tmp_path):
    proj = tmp_path / "myproject"
    proj.mkdir()
    health = build_workspace_health(str(proj))
    assert health["exists"] is True
    assert health["writable"] is True
    assert health["is_git_repo"] is False
    assert "run_count" in health


def test_get_set_workspace_settings(tmp_path):
    proj = tmp_path / "myproject"
    proj.mkdir()
    settings = get_workspace_settings(str(proj))
    assert "model" in settings
    assert "safety" in settings

    saved = save_workspace_settings({"model": {"provider": "deepseek"}}, str(proj))
    assert saved["model"]["provider"] == "deepseek"

    reloaded = get_workspace_settings(str(proj))
    assert reloaded["model"]["provider"] == "deepseek"


def test_get_workspace_settings_defaults(tmp_path):
    proj = tmp_path / "myproject"
    proj.mkdir()
    settings = get_workspace_settings(str(proj))
    assert settings["safety"]["require_approval_for_shell"] is True
    assert settings["safety"]["require_approval_for_file_delete"] is True
    assert "node_modules" in settings["indexing"]["ignore"]


# --- E2 State machine / RunManager tests ---

def test_state_machine_valid_transitions():
    sm = RunStateMachine(RunStatus.CREATED)
    sm.transition(RunStatus.RUNNING)
    assert sm.status == RunStatus.RUNNING
    sm.transition(RunStatus.VALIDATING)
    assert sm.status == RunStatus.VALIDATING
    sm.transition(RunStatus.COMPLETED)
    assert sm.status == RunStatus.COMPLETED
    assert sm.is_terminal()


def test_state_machine_invalid_transition_raises():
    sm = RunStateMachine(RunStatus.RUNNING)
    try:
        sm.transition(RunStatus.CREATED)
    except ValueError as exc:
        assert "不允许的状态转移" in str(exc)
    else:
        raise AssertionError("invalid transition should raise ValueError")


def test_state_machine_terminal():
    sm = RunStateMachine(RunStatus.COMPLETED)
    assert sm.is_terminal()
    sm2 = RunStateMachine(RunStatus.CANCELLED)
    assert sm2.is_terminal()
    sm3 = RunStateMachine(RunStatus.RUNNING)
    assert not sm3.is_terminal()


def test_state_machine_history():
    sm = RunStateMachine(RunStatus.CREATED)
    sm.transition(RunStatus.PLANNING)
    sm.transition(RunStatus.RUNNING)
    assert sm.history() == [RunStatus.CREATED, RunStatus.PLANNING, RunStatus.RUNNING]


def test_run_manager_detect_interrupted(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("orphan-run", "Test", str(workspace), status="running")
    rm = RunManager()
    interrupted = rm.detect_interrupted(str(workspace))
    assert "orphan-run" in interrupted
    session = store.get_session("orphan-run", str(workspace))
    assert session["status"] == "interrupted"


def test_run_manager_register_unregister(tmp_path):
    from unittest.mock import MagicMock
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = MagicMock()
    ctx.thread_id = "test-run"
    ctx.workspace_dir = str(workspace)
    ctx.metadata = {}

    rm = RunManager()
    rm.register(ctx)
    assert rm.get("test-run") is not None
    rm.unregister("test-run")
    assert rm.get("test-run") is None


def test_run_manager_rejects_second_write_run_same_workspace(tmp_path):
    from unittest.mock import MagicMock

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = MagicMock()
    first.thread_id = "run-1"
    first.workspace_dir = str(workspace)
    first.metadata = {"mode": "agenthub_delivery"}

    second = MagicMock()
    second.thread_id = "run-2"
    second.workspace_dir = str(workspace)
    second.metadata = {"mode": "agenthub_delivery"}

    rm = RunManager()
    rm.register(first)
    try:
        rm.register(second)
    except ValueError as exc:
        assert "同时只允许一个写入型 run" in str(exc)
    else:
        raise AssertionError("second write run should be rejected")
    assert rm.get("run-2") is None


def test_event_schema_enrich():
    raw = {"type": "done", "title": "完成"}
    enriched = enrich_event(raw, thread_id="t1")
    assert enriched["schema_version"] == 1
    assert enriched["thread_id"] == "t1"
    assert "event_id" in enriched
    assert "created_at" in enriched
    assert enriched["severity"] == "info"

    error_event = enrich_event({"type": "error"}, thread_id="t2")
    assert error_event["severity"] == "error"


# --- E3 Strategy / Context Pack tests ---

def test_context_pack_to_text():
    pack = ContextPack(
        task_summary="修复导入错误",
        relevant_files=["src/app.py", "tests/test_app.py"],
        selected_skills=["skill.delivery-review"],
    )
    text = pack.to_text()
    assert "修复导入错误" in text
    assert "src/app.py" in text
    assert "skill.delivery-review" in text
    assert "Token 预算" in text


def test_context_pack_estimate_tokens():
    pack = ContextPack(task_summary="A" * 300)
    assert pack.estimate_tokens() > 50


def test_select_strategy_bug_fix():
    assert select_strategy("帮我修复一个导入错误") == "bug_fix"
    assert select_strategy("fix the bug in login") == "bug_fix"


def test_select_strategy_small_patch():
    assert select_strategy("改个配置项") == "small_patch"
    assert select_strategy("typo fix") == "small_patch"


def test_select_strategy_docs_only():
    assert select_strategy("写一下README文档") == "docs_only"


def test_select_strategy_default():
    assert select_strategy("帮我做一个完整的用户登录功能") == "feature_delivery"


def test_select_strategy_code_task_with_usage_notes():
    assert select_strategy("新建 todo.py 和 tests/test_todo.py，运行 pytest，并说明如何使用") == "feature_delivery"


def test_tool_policy_check():
    policy = ToolPolicy(
        allowed_tools=["read_file", "write_file"],
        denied_tools=["delete_file"],
    )
    assert policy.check("read_file") is True
    assert policy.check("delete_file") is False
    assert policy.check("unknown_tool") is False


def test_tool_policy_budget():
    policy = ToolPolicy(
        allowed_tools=["read_file"],
        budgets={"max_tool_calls": 5, "max_file_writes": 2},
    )
    assert policy.within_budget(3, 1) is True
    assert policy.within_budget(6, 0) is False
    assert policy.within_budget(3, 3) is False


# --- E4 Checkpoint / Git sandbox tests ---

def test_create_checkpoint(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('hello')", encoding="utf-8")
    meta = create_checkpoint(
        filepath="app.py", reason="before edit", stage_id="implement",
        thread_id="test-run", workspace_dir=str(workspace),
    )
    assert meta["filepath"] == "app.py"
    assert meta["reason"] == "before edit"
    checkpoints_dir = workspace / ".checkpoints" / "test-run"
    assert checkpoints_dir.exists()


def test_list_checkpoints(tmp_path):
    import time as _time
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("v1", encoding="utf-8")
    create_checkpoint("app.py", "v1", "implement", "test-run", str(workspace))
    _time.sleep(0.01)
    (workspace / "app.py").write_text("v2", encoding="utf-8")
    create_checkpoint("app.py", "v2", "implement", "test-run", str(workspace))

    result = list_checkpoints("test-run", str(workspace))
    assert result["total"] == 2
    assert "app.py" in result["files"]


def test_restore_checkpoint(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("original", encoding="utf-8")
    meta = create_checkpoint("app.py", "backup", "implement", "test-run", str(workspace))
    (workspace / "app.py").write_text("modified", encoding="utf-8")

    result = restore_checkpoint(meta["checkpoint_id"], "test-run", confirmed=True, workspace_dir=str(workspace))
    assert result["restored"] is True
    assert (workspace / "app.py").read_text(encoding="utf-8") == "original"


def test_restore_checkpoint_requires_confirmed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("x", encoding="utf-8")
    meta = create_checkpoint("app.py", "test", "s", "r", str(workspace))
    try:
        restore_checkpoint(meta["checkpoint_id"], "r", confirmed=False, workspace_dir=str(workspace))
    except ValueError as exc:
        assert "confirmed" in str(exc)
    else:
        raise AssertionError("should require confirmed")


def test_checkpoint_rejects_path_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')", encoding="utf-8")

    try:
        create_checkpoint("../outside.py", "escape", "s", "r", str(workspace))
    except ValueError as exc:
        assert "路径越界" in str(exc)
    else:
        raise AssertionError("path escape should be rejected")


def test_recovery_action_risk_levels():
    assert _action_risk_level("inspect-failed-stage") == "safe"
    assert _action_risk_level("rerun-tests") == "guarded"
    assert _action_risk_level("restore-backup") == "destructive"
    assert _action_risk_level("continue-delivery") == "safe"


# --- E5 Eval / Metrics tests ---

def test_list_evals_returns_catalog():
    evals = list_evals()
    assert len(evals) >= 2
    ids = [e["id"] for e in evals]
    assert "todo_web_app" in ids
    assert "bug_fix_import_error" in ids


def test_run_eval_scores_against_signals(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    result = run_eval("todo_web_app", str(workspace), store)
    assert "score" in result
    assert result["eval_id"] == "todo_web_app"
    assert result["score"]["overall"] == "passed"


def test_build_aggregate_metrics(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runs_dir = workspace / ".nanocursor" / "runs" / "test-run"
    runs_dir.mkdir(parents=True)
    (runs_dir / "session.json").write_text(
        json.dumps({"thread_id": "test-run", "status": "completed", "prompt": "test"}),
        encoding="utf-8",
    )
    (runs_dir / "events.jsonl").write_text(
        json.dumps({"type": "tool_call_finished", "payload": {"metrics": {"total_llm_tokens": 500}}}) + "\n" +
        json.dumps({"type": "tool_call_finished", "payload": {"metrics": {"total_llm_tokens": 300}}}) + "\n",
        encoding="utf-8",
    )
    metrics = build_aggregate_metrics(str(workspace))
    assert metrics["total_runs"] == 1
    assert metrics["completed"] == 1
    assert metrics["avg_tool_calls"] == 2.0
    assert metrics["avg_tokens"] == 800


def test_execute_recovery_center_recommended_actions_are_supported(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = EventStore()
    store.create_session("test-run", "Test", str(workspace), status="failed")
    store.update_session("test-run", str(workspace), execution_plan={
        "stages": [
            {"id": "implement", "title": "实现", "status": "failed", "failure": "tool error", "tool_evidence": []},
        ]
    })
    store.append_event("test-run", "error", content="AssertionError: boom", workspace_dir=str(workspace))

    center = build_recovery_center("test-run", str(workspace))
    supported_results = []
    for action in center["actions"]:
        if not action.get("enabled"):
            continue
        supported_results.append(
            execute_recovery_action(
                "test-run",
                action["id"],
                action.get("target", ""),
                False,
                str(workspace),
            )
        )

    assert supported_results
    assert all(result["status"] in {"completed", "failed"} for result in supported_results)


def test_execute_restore_backup_uses_explicit_target_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backups = workspace / ".backups"
    backups.mkdir()
    (backups / "app.py.bak.1").write_text("print('restored')\n", encoding="utf-8")
    store = EventStore()
    store.create_session("test-run", "Test", str(workspace))

    result = execute_recovery_action(
        "test-run",
        "restore-backup",
        "app.py.bak.1",
        True,
        str(workspace),
        target_path="src/app.py",
    )

    assert result["ok"] is True
    assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "print('restored')\n"
