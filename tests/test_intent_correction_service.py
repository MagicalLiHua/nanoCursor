from fastapi.testclient import TestClient

from src.api.services.intent_correction_service import correct_run_intent
from src.api.services.intent_router import classify_user_intent


def test_correct_run_intent_upgrades_direct_answer_to_feature_delivery(tmp_path):
    from src.api.services.agent_loop_state_service import get_agent_loop_state, init_agent_loop_state
    from src.api.services.event_store import get_event_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "intent-correct-upgrade"
    initial_intent = classify_user_intent("你好")
    store = get_event_store()
    store.create_session(thread_id, "你好", str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        intent_decision=initial_intent,
        intent_decision_normalized=initial_intent,
        intent_corrections=[],
        execution_plan={"strategy": "analysis_only", "intent_decision": initial_intent, "summary": {}},
    )
    init_agent_loop_state(thread_id, str(workspace), user_request="你好", intent=initial_intent)

    result = correct_run_intent(
        thread_id,
        str(workspace),
        route="feature_delivery",
        complexity="small_code",
        reason="用户补充说明这其实是一个代码生成任务。",
        evidence={"message_id": "m2"},
    )

    assert result["intent_decision"]["route"] == "feature_delivery"
    assert result["intent_decision"]["execution_route"] == "agenthub_delivery"
    assert result["intent_decision"]["requires_workspace_write"] is True
    session = store.get_session(thread_id, str(workspace))
    assert session["intent_decision_normalized"]["route"] == "feature_delivery"
    assert session["intent_corrections"][0]["old_route"] == "direct_answer"
    assert session["intent_corrections"][0]["new_route"] == "feature_delivery"
    assert session["execution_plan"]["summary"]["intent_route"] == "feature_delivery"
    events = store.list_events(thread_id, str(workspace))
    assert any(event.type == "intent_route_corrected" for event in events)
    loop = get_agent_loop_state(thread_id, str(workspace))
    assert loop["intent"]["route"] == "feature_delivery"
    assert loop["steps"][-1]["phase"] == "decide"


def test_high_risk_guard_blocks_unsafe_downgrade(tmp_path):
    from src.api.services.event_store import get_event_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "intent-correct-risky"
    initial_intent = classify_user_intent("帮我删除 node_modules 并 git push")
    store = get_event_store()
    store.create_session(thread_id, "帮我删除 node_modules 并 git push", str(workspace), status="running")
    store.update_session(
        thread_id,
        str(workspace),
        intent_decision=initial_intent,
        intent_decision_normalized=initial_intent,
        intent_corrections=[],
    )

    result = correct_run_intent(
        thread_id,
        str(workspace),
        route="read_only",
        reason="尝试降级为只读。",
    )

    assert result["intent_decision"]["route"] == "risky_operation"
    assert result["intent_decision"]["requires_approval"] is True
    assert result["intent_decision"]["risk_level"] == "high"
    assert "high_risk_guard" in result["intent_decision"]["guard_hits"]


def test_intent_correction_api_route(tmp_path):
    from src.api import legacy_runtime as api_server
    from src.api.services.event_store import get_event_store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread_id = "intent-correct-api"
    original_workspace = api_server._get_workspace()
    try:
        api_server._set_active_workspace(str(workspace))
        initial_intent = classify_user_intent("帮我看看这个项目结构")
        store = get_event_store()
        store.create_session(thread_id, "帮我看看这个项目结构", str(workspace), status="running")
        store.update_session(
            thread_id,
            str(workspace),
            intent_decision=initial_intent,
            intent_decision_normalized=initial_intent,
            intent_corrections=[],
        )
        client = TestClient(api_server.app)

        response = client.post(
            f"/api/runs/{thread_id}/intent/correct",
            json={
                "route": "small_edit",
                "complexity": "small_code",
                "reason": "Lead 读取后确认需要改 README。",
                "evidence": {"file": "README.md"},
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["intent_decision"]["route"] == "small_edit"
        assert body["correction"]["old_route"] == "read_only"
    finally:
        api_server._set_active_workspace(original_workspace)
