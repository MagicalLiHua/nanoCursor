import asyncio


def test_runtime_turn_persists_context_action_and_result(tmp_path):
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.api.services.runtime_turn_service import RuntimeTurnResult, run_runtime_turn

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    thread_id = "runtime-turn-direct"
    store = get_event_store()
    intent = classify_user_intent("哈喽")
    store.create_session(thread_id, "哈喽", str(workspace), status="running")
    store.update_session(thread_id, str(workspace), intent_decision=intent, execution_plan={"intent_decision": intent})
    init_agent_loop_state(thread_id, str(workspace), user_request="哈喽", intent=intent)

    async def executor(action, context_pack):
        assert action.type == "answer"
        assert context_pack["id"]
        return {"executed": True, "result": "success", "output": "你好"}

    result = asyncio.run(
        run_runtime_turn(
            thread_id,
            str(workspace),
            action={"type": "answer", "goal": "reply", "agent": "Lead"},
            executor=executor,
        )
    )

    assert isinstance(result, RuntimeTurnResult)
    assert result.step == 1
    assert result.context_pack_id
    assert result.selected_action.type == "answer"
    assert result.execution_result["output"] == "你好"
    events = store.list_events(thread_id, str(workspace))
    event_types = [event.type for event in events]
    assert "loop_turn_started" in event_types
    assert "loop_context_built" in event_types
    assert "loop_action_proposed" in event_types
    assert "loop_action_executed" in event_types
    assert "loop_turn_finished" in event_types
    turn_event = next(event for event in events if event.type == "loop_turn_finished")
    assert set(["turn_id", "step", "task_id", "context_pack_id", "action_type"]).issubset(turn_event.payload)


def test_runtime_turn_read_only_rejects_write_action(tmp_path):
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.api.services.runtime_turn_service import run_runtime_turn

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "runtime-turn-read-only"
    store = get_event_store()
    intent = classify_user_intent("帮我看看当前项目结构")
    store.create_session(thread_id, "帮我看看当前项目结构", str(workspace), status="running")
    store.update_session(thread_id, str(workspace), intent_decision=intent, execution_plan={"intent_decision": intent})
    init_agent_loop_state(thread_id, str(workspace), user_request="帮我看看当前项目结构", intent=intent)

    result = asyncio.run(
        run_runtime_turn(
            thread_id,
            str(workspace),
            action={
                "type": "call_tool",
                "goal": "write a file",
                "agent": "Lead",
                "tool_call": {"tool": "write_file", "input": {"path": "bad.txt", "content": "bad"}},
            },
            execute_tools=True,
        )
    )

    assert result.repaired is True
    assert result.selected_action.type == "inspect_project"
    assert result.execution_result["executed"] is False
    assert result.execution_result["result"] == "not_requested"
    assert not (workspace / "bad.txt").exists()


def test_runtime_turn_context_pack_includes_turn_observation(tmp_path):
    from src.api.services.agent_loop_state_service import init_agent_loop_state
    from src.api.services.event_store import get_event_store
    from src.api.services.intent_router import classify_user_intent
    from src.api.services.run_state_service import get_context_pack_by_id
    from src.api.services.runtime_turn_service import context_pack_to_text, run_runtime_turn
    from src.runtime.task_board import RunTask, RunTaskBoard, save_task_board

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# demo\n\nTODO: add feature docs\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "feature.py").write_text("def feature():\n    return 'todo'\n", encoding="utf-8")
    (workspace / "test_feature.py").write_text(
        "from src.feature import feature\n\n"
        "def test_feature():\n"
        "    assert feature() == 'done'\n",
        encoding="utf-8",
    )
    thread_id = "runtime-turn-context-observation"
    store = get_event_store()
    prompt = "帮我根据 README 实现一个小功能"
    intent = classify_user_intent(prompt)
    store.create_session(thread_id, prompt, str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        intent_decision=intent,
        execution_plan={"strategy": "feature_delivery", "intent_decision": intent},
    )
    init_agent_loop_state(thread_id, str(workspace), user_request=prompt, intent=intent)
    save_task_board(
        RunTaskBoard(
            run_id=thread_id,
            strategy="feature_delivery",
            nodes=[
                RunTask(
                    id="task-001-implement",
                    type="implementation",
                    title="实现 README 中的小功能",
                    goal="读取 README 并实现对应功能",
                    status="ready",
                    agent_role="coder",
                    acceptance=[
                        {
                            "id": "feature_implemented",
                            "description": "src/feature.py 中的功能已经实现。",
                        }
                    ],
                    evidence=[
                        {
                            "kind": "file_change",
                            "content": "创建了功能实现文件。",
                            "path": "src/feature.py",
                            "changed_files": ["src/feature.py"],
                        }
                    ],
                ),
                RunTask(
                    id="task-002-verify",
                    type="test",
                    title="验证功能实现",
                    goal="运行 test_feature.py 并修复失败",
                    status="failed",
                    agent_role="tester",
                    evidence=[
                        {
                            "kind": "test",
                            "content": "FAILED test_feature.py::test_feature",
                            "path": "test_feature.py",
                        }
                    ],
                ),
            ],
        ),
        workspace / ".nanocursor" / "runs" / thread_id,
    )
    store.append_event(
        thread_id,
        "tool_call_finished",
        title="读取 README.md",
        content="README.md 已读取",
        agent="coder",
        payload={
            "task_id": "task-001-implement",
            "tool": "read_file",
            "target": "README.md",
            "status": "success",
            "summary": "README mentions feature docs.",
            "changed_files": ["src/feature.py"],
        },
        workspace_dir=str(workspace),
    )

    result = asyncio.run(
        run_runtime_turn(
            thread_id,
            str(workspace),
            action={
                "type": "inspect_project",
                "goal": "继续观察当前任务和工具结果",
                "agent": "Lead",
                "task_id": "task-001-implement",
            },
        )
    )

    pack = get_context_pack_by_id(thread_id, str(workspace), result.context_pack_id or "")
    turn_context = pack["turn_context"]
    assert pack["purpose"] == "lead_turn"
    assert pack["task_id"] == "task-001-implement"
    assert turn_context["active_task"]["id"] == "task-001-implement"
    assert turn_context["active_task"]["title"] == "实现 README 中的小功能"
    assert turn_context["active_task"]["acceptance"][0]["description"] == "src/feature.py 中的功能已经实现。"
    assert turn_context["active_task"]["recent_evidence"][0]["path"] == "src/feature.py"
    assert turn_context["failed_tasks"][0]["id"] == "task-002-verify"
    assert turn_context["failed_tasks"][0]["recent_evidence"][0]["path"] == "test_feature.py"
    assert turn_context["recent_tool_results"][0]["tool"] == "read_file"
    assert turn_context["recent_tool_results"][0]["target"] == "README.md"
    assert turn_context["changed_files"] == ["src/feature.py"]
    assert "src/feature.py" in pack["relevant_files"]
    assert "test_feature.py" in pack["relevant_files"]
    assert pack["context_debug"]["turn_context"]["recent_tool_result_count"] == 1
    rendered = context_pack_to_text(pack)
    assert "本轮观察" in rendered
    assert "实现 README 中的小功能" in rendered
    assert "src/feature.py 中的功能已经实现" in rendered
    assert "turn_changed_files: src/feature.py" in rendered
    assert "failed_or_blocked_tasks" in rendered
    assert "验证功能实现" in rendered
    assert "read_file" in rendered


def test_context_pack_to_text_ignores_persistence_metadata():
    from src.api.services.runtime_turn_service import context_pack_to_text

    text = context_pack_to_text(
        {
            "id": "pack-1",
            "thread_id": "run-1",
            "task_summary": "Inspect README",
            "relevant_files": ["README.md"],
        }
    )

    assert "Inspect README" in text
    assert "README.md" in text
