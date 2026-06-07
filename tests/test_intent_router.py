import asyncio

from src.api.services.intent_router import classify_user_intent, classify_user_intent_async, is_lead_direct_intent


def test_greeting_routes_to_lead_direct_reply():
    decision = classify_user_intent("你好，你是什么模型")

    assert decision["intent"] == "greeting"
    assert decision["route"] == "direct_answer"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["requires_workspace_write"] is False
    assert decision["requires_workspace_read"] is False
    assert decision["guard_hits"]
    assert decision["suggested_agent_specs"][0]["role"] == "Lead"
    assert "P0" not in " ".join(decision.get("acceptance_criteria", []))
    assert is_lead_direct_intent("你好，你是什么模型") is True
    assert is_lead_direct_intent("哈喽") is True


def test_greeting_with_negated_file_change_stays_lead_direct():
    decision = classify_user_intent("哈喽，简单介绍一下你能做什么，不要修改文件")

    assert decision["route"] == "direct_answer"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["requires_workspace_write"] is False
    assert "write_negated" in decision["signals"]
    assert "write_action" not in decision["signals"]


def test_greeting_with_negated_file_access_stays_lead_direct():
    decision = classify_user_intent("哈喽，请用一句话介绍你自己，不要读取或修改文件。")

    assert decision["route"] == "direct_answer"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["requires_workspace_read"] is False
    assert decision["requires_workspace_write"] is False
    assert "workspace_read_negated" in decision["signals"]
    assert "workspace_read" not in decision["signals"]
    assert "write_action" not in decision["signals"]


def test_short_python_artifact_request_routes_to_code_execution():
    decision = classify_user_intent("帮我用python写常见的排序算法并比较性能")

    assert decision["intent"] == "code_generation"
    assert decision["level"] == "small_code"
    assert decision["route"] == "feature_delivery"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["requires_shell"] is True
    assert "code_artifact" in decision["signals"]
    assert decision["context_requirements"]["need_project_index"] is True
    assert decision["tool_permissions"]["write_file"] == "safe_write"
    assert is_lead_direct_intent("帮我用python写常见的排序算法并比较性能") is False


def test_read_only_explanation_stays_lightweight():
    decision = classify_user_intent("解释一下快速排序为什么平均复杂度是 nlogn")

    assert decision["route"] == "direct_answer"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["requires_workspace_write"] is False


def test_high_risk_prompt_escalates():
    decision = classify_user_intent("重构认证权限并处理数据库 schema 迁移和回滚风险")

    assert decision["level"] == "high_risk"
    assert decision["route"] == "risky_operation"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["requires_approval"] is True


def test_project_structure_routes_to_read_only():
    decision = classify_user_intent("帮我看看这个项目结构")

    assert decision["route"] == "read_only"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_workspace_read"] is True
    assert decision["requires_workspace_write"] is False


def test_small_readme_typo_routes_to_small_edit():
    decision = classify_user_intent("帮我改 README 的错别字")

    assert decision["route"] == "small_edit"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["suggested_agents"] == ["Lead", "Coder"]


def test_ambiguous_improve_request_asks_for_clarification():
    decision = classify_user_intent("帮我优化一下")

    assert decision["route"] == "clarification_needed"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["missing_information"]


def test_test_only_route_requires_shell_without_write():
    decision = classify_user_intent("帮我运行 pytest 验证一下")

    assert decision["route"] == "test_only"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_shell"] is True
    assert decision["requires_workspace_write"] is False


def test_followup_uses_conversation_memory_for_code_task():
    without_memory = classify_user_intent("继续")
    with_memory = classify_user_intent(
        "继续",
        conversation_summary="上一轮用户要求实现 Python 排序算法性能比较脚本，已经进入代码任务并准备写文件。",
    )

    assert without_memory["execution_route"] == "lead_direct_reply"
    assert with_memory["intent"] == "conversation_followup"
    assert with_memory["route"] == "feature_delivery"
    assert with_memory["execution_route"] == "agenthub_delivery"
    assert with_memory["requires_workspace_read"] is True
    assert with_memory["requires_workspace_write"] is True
    assert "conversation_followup" in with_memory["signals"]
    assert "coding_conversation_context" in with_memory["signals"]


def test_followup_uses_conversation_memory_for_test_context():
    decision = classify_user_intent(
        "接着",
        conversation_summary="上一轮正在运行 pytest 验证登录模块，测试结果还没整理。",
    )

    assert decision["intent"] == "conversation_followup"
    assert decision["route"] == "test_only"
    assert decision["requires_shell"] is True
    assert decision["requires_workspace_write"] is False


def test_conversation_meta_question_stays_direct_even_with_code_history():
    decision = classify_user_intent(
        "上一条消息里我问了什么？请用一句话回答，不要修改文件。",
        conversation_summary="上一轮用户要求实现 Python 排序算法性能比较脚本，已经进入代码任务并准备写文件。",
    )

    assert decision["intent"] == "conversation_meta_question"
    assert decision["route"] == "direct_answer"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["requires_workspace_read"] is False
    assert decision["requires_workspace_write"] is False
    assert "conversation_meta_question" in decision["signals"]


def test_async_intent_router_normalizes_llm_result(monkeypatch):
    async def fake_classifier(prompt, conversation_summary="", workspace_summary=None):
        return {
            "strategy": "feature_delivery",
            "complexity": "medium",
            "needed_roles": ["lead", "coder", "tester"],
            "confidence": 0.91,
            "rationale": "需要生成代码并验证。",
        }

    monkeypatch.setattr("src.agent.strategy.classifier.classify_with_llm", fake_classifier)

    decision = asyncio.run(classify_user_intent_async("请实现一个数据导入模块，并补充验证"))

    assert decision["source"] == "normalized_llm_intent"
    assert decision["normalized_from"] == "llm_structured_intent"
    assert decision["route"] == "feature_delivery"
    assert decision["complexity"] == "medium"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["requires_shell"] is True
    assert decision["raw_decision"]["raw_llm_result"]["strategy"] == "feature_delivery"
    assert any(agent["role"] == "Coder" for agent in decision["suggested_agent_specs"])


def test_high_risk_guard_overrides_llm(monkeypatch):
    async def fake_classifier(prompt, conversation_summary="", workspace_summary=None):
        return {
            "strategy": "analysis_only",
            "complexity": "simple",
            "needed_roles": ["lead"],
            "confidence": 0.99,
            "rationale": "模型误判为简单分析。",
        }

    monkeypatch.setattr("src.agent.strategy.classifier.classify_with_llm", fake_classifier)

    decision = asyncio.run(classify_user_intent_async("帮我删除 node_modules 并 git push"))

    assert decision["route"] == "risky_operation"
    assert decision["complexity"] == "high_risk"
    assert decision["requires_approval"] is True
    assert decision["risk_level"] == "high"
    assert "high_risk_guard" in decision["guard_hits"]
    assert decision["normalized_from"] == "hard_guard"
