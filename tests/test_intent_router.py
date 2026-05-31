from src.api.services.intent_router import classify_user_intent, is_lead_direct_intent


def test_greeting_routes_to_lead_direct_reply():
    decision = classify_user_intent("你好，你是什么模型")

    assert decision["intent"] == "greeting"
    assert decision["route"] == "lead_direct_reply"
    assert decision["requires_workspace_write"] is False
    assert is_lead_direct_intent("你好，你是什么模型") is True


def test_short_python_artifact_request_routes_to_code_execution():
    decision = classify_user_intent("帮我用python写常见的排序算法并比较性能")

    assert decision["intent"] == "code_generation"
    assert decision["level"] == "small_code"
    assert decision["route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["requires_execution"] is True
    assert "code_artifact" in decision["signals"]
    assert is_lead_direct_intent("帮我用python写常见的排序算法并比较性能") is False


def test_read_only_explanation_stays_lightweight():
    decision = classify_user_intent("解释一下快速排序为什么平均复杂度是 nlogn")

    assert decision["route"] == "lead_direct_reply"
    assert decision["requires_workspace_write"] is False


def test_high_risk_prompt_escalates():
    decision = classify_user_intent("重构认证权限并处理数据库 schema 迁移和回滚风险")

    assert decision["level"] == "high_risk"
    assert decision["route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
