import asyncio

from src.api.services.intent_router import (
    classify_user_intent,
    classify_user_intent_async,
    is_lead_direct_intent,
)


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
    assert decision["raw_decision"]["router_trace"]["final_route"] == "direct_answer"
    assert "explicit_no_write_enforced" in decision["raw_decision"]["router_trace"]["normalization_notes"]


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
    trace = decision["raw_decision"]["router_trace"]
    assert "code_artifact_hint" in trace["deterministic_hints"]
    assert "tooling_hint" in trace["deterministic_hints"]
    assert is_lead_direct_intent("帮我用python写常见的排序算法并比较性能") is False


def test_read_only_explanation_stays_lightweight():
    decision = classify_user_intent("解释一下快速排序为什么平均复杂度是 nlogn")

    assert decision["route"] == "direct_answer"
    assert decision["execution_route"] == "lead_direct_reply"
    assert decision["requires_workspace_write"] is False


def test_technical_comparison_stays_lead_direct():
    for prompt in [
        "python和java谁更好",
        "Python 和 Java 哪个更适合初学者",
        "你觉得 React 和 Vue 怎么选",
        "帮我比较一下 Python 和 Java 的优缺点",
    ]:
        decision = classify_user_intent(prompt)
        assert decision["route"] == "direct_answer"
        assert decision["execution_route"] == "lead_direct_reply"
        assert decision["requires_workspace_write"] is False
        assert "discussion_or_comparison" in decision["signals"]


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


def test_folder_file_listing_stays_read_only():
    decision = classify_user_intent("帮我看看这个文件夹下面都有什么文件")

    assert decision["route"] == "read_only"
    assert decision["requires_workspace_read"] is True
    assert decision["requires_workspace_write"] is False


def test_read_only_write_word_phrases_do_not_trigger_write_action():
    for prompt in [
        "帮我看看项目说明写了什么",
        "帮我看看最近改动情况",
        "看一下 docs 目录主要写了什么",
    ]:
        decision = classify_user_intent(prompt)
        assert decision["route"] == "read_only"
        assert decision["requires_workspace_read"] is True
        assert decision["requires_workspace_write"] is False
        assert "write_action" not in decision["signals"]


def test_code_correctness_review_stays_read_only():
    for prompt in [
        "你帮我看看这个路径下都有一些什么文件 有没有什么涉及到算法的代码 代码写的对不对",
        "你帮我看看这个路径下都有什么文件 有没有什么涉及到算法的代码 代码写的不对",
        "帮我检查一下这里的代码写得对不对",
        "帮我看看这个文件夹有没有算法代码，代码有没有问题",
    ]:
        decision = classify_user_intent(prompt)
        assert decision["route"] == "review_only"
        assert decision["requires_workspace_read"] is True
        assert decision["requires_workspace_write"] is False
        assert "review_or_risk_check" in decision["signals"]
        assert "write_action" not in decision["signals"]


def test_review_question_with_explicit_fix_still_allows_write():
    decision = classify_user_intent("帮我检查代码写得对不对，并修复问题")

    assert decision["requires_workspace_write"] is True
    assert decision["route"] in {"small_edit", "feature_delivery"}
    assert "write_action" in decision["signals"]


def test_small_readme_typo_routes_to_small_edit():
    decision = classify_user_intent("帮我改 README 的错别字")

    assert decision["route"] == "small_edit"
    assert decision["execution_route"] == "agenthub_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["suggested_agents"] == ["Lead", "Coder"]


def test_delete_word_is_not_always_high_risk():
    decision = classify_user_intent("帮我删除 README 里多余的一句话")

    assert decision["route"] == "small_edit"
    assert decision["risk_level"] != "high"
    assert decision["requires_approval"] is False


def test_deleting_named_file_still_escalates():
    decision = classify_user_intent("删除 old.py")

    assert decision["route"] == "risky_operation"
    assert decision["risk_level"] == "high"
    assert decision["requires_approval"] is True


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
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "disabled")

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
