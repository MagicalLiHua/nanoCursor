import json

from src.api.services.intent_runtime_context import context_from_conversation


def test_context_from_conversation_uses_run_records_and_session(tmp_path):
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".nanocursor" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (workspace / ".git").mkdir()
    (run_dir / "session.json").write_text(
        json.dumps(
            {
                "status": "running",
                "intent_decision": {"route": "debug_fix"},
                "execution_plan": {"current_stage": "verify"},
                "selected_files": ["src/app.py"],
                "events": [
                    {
                        "event_type": "tool_finished",
                        "payload": {"tool_name": "pytest", "status": "failed"},
                    }
                ],
                "approvals": [{"status": "pending"}],
                "has_uncommitted_diff": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    conversation = {
        "conversation_id": "conv-1",
        "workspace_dir": str(workspace),
        "conversation_summary": "上一轮在修复测试失败。",
        "current_thread_id": "run-1",
        "run_records": [
            {"thread_id": "run-0", "status": "completed", "prompt": "旧任务"},
            {"thread_id": "run-1", "status": "running", "prompt": "修复测试"},
        ],
        "messages": [
            {"role": "user", "content": "帮我修测试"},
            {"role": "assistant", "content": "我开始检查。"},
        ],
        "conversation_memory": {"changed_files": ["tests/test_app.py", "src/app.py"]},
        "summary_stats": {"changed_file_count": 2},
    }

    context = context_from_conversation(conversation, prompt="继续", workspace_dir=str(workspace))

    assert context.conversation_id == "conv-1"
    assert context.thread_id == "run-1"
    assert context.last_intent_route == "debug_fix"
    assert context.active_run_status == "running"
    assert context.active_run_stage == "verify"
    assert context.last_tool_name == "pytest"
    assert context.last_tool_status == "failed"
    assert context.has_pending_approval is True
    assert context.has_uncommitted_diff is True
    assert context.changed_file_count == 2
    assert context.selected_files == ["src/app.py"]
    assert context.recent_files == ["tests/test_app.py", "src/app.py"]
    assert context.workspace_is_git is True
