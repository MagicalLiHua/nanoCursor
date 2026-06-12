import asyncio

from src.api.services.intent_router import classify_user_intent_async
from src.api.services.semantic_intent_classifier import (
    clear_semantic_intent_cache,
    parse_semantic_intent_response,
    semantic_intent_mode,
    semantic_low_confidence_clarify,
    semantic_require_llm,
)


def test_semantic_parser_accepts_structured_json():
    result = parse_semantic_intent_response(
        """
        {
          "route": "read_only",
          "confidence": 0.86,
          "complexity": "simple",
          "intent_summary": "查看项目结构",
          "user_goal": "了解当前目录",
          "needs_workspace_read": true,
          "needs_workspace_write": false,
          "needs_shell": false,
          "needs_approval": false,
          "risk_level": "low",
          "risk_reasons": [],
          "suggested_agents": ["lead"],
          "expected_artifacts": [],
          "missing_information": [],
          "reasoning": "需要读取目录但不修改文件"
        }
        """
    )

    assert result is not None
    assert result["route"] == "read_only"
    assert result["requires_workspace_read"] is True
    assert result["requires_workspace_write"] is False
    assert result["suggested_agents"] == ["Lead"]
    assert result["source"] == "semantic_intent_classifier"


def test_semantic_parser_accepts_fenced_json():
    result = parse_semantic_intent_response(
        """```json
        {
          "route": "direct_answer",
          "confidence": 0.91,
          "complexity": "simple",
          "needs_workspace_read": false,
          "needs_workspace_write": false,
          "needs_shell": false,
          "needs_approval": false,
          "suggested_agents": ["Lead"]
        }
        ```"""
    )

    assert result is not None
    assert result["route"] == "direct_answer"
    assert result["requires_workspace_read"] is False


def test_semantic_parser_rejects_invalid_payload():
    assert parse_semantic_intent_response("not json") is None
    assert parse_semantic_intent_response('{"route":"unknown","confidence":2}') is None


def test_semantic_mode_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_SEMANTIC_INTENT_MODE", raising=False)
    monkeypatch.delenv("NANOCURSOR_SEMANTIC_INTENT_ENABLED", raising=False)

    assert semantic_intent_mode() == "enabled"


def test_semantic_mode_can_be_disabled_with_legacy_flag(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_SEMANTIC_INTENT_MODE", raising=False)
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_ENABLED", "false")

    assert semantic_intent_mode() == "disabled"


def test_semantic_enabled_respects_explicit_no_write(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "feature_delivery",
            "complexity": "medium",
            "confidence": 0.94,
            "requires_workspace_read": True,
            "requires_workspace_write": True,
            "requires_shell": True,
            "requires_approval": False,
            "suggested_agents": ["Lead", "Coder"],
            "rationale": "模型认为可以实现代码。",
            "intent": "semantic_code_plan",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("给我一个登录模块实现方案，不要改代码"))

    assert decision["requires_workspace_write"] is False
    assert decision["route"] in {"direct_answer", "read_only"}
    assert "explicit_no_write_enforced" in decision["indicators"]
    assert decision["raw_decision"]["router_trace"]["semantic_route"] == "feature_delivery"


def test_semantic_direct_answer_cannot_downgrade_strong_code_task(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "direct_answer",
            "complexity": "simple",
            "confidence": 0.95,
            "requires_workspace_read": False,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead"],
            "rationale": "模型误以为只是解释算法。",
            "intent": "semantic_direct_answer",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("帮我用 Python 写常见排序算法并比较性能"))

    assert decision["route"] == "feature_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["requires_shell"] is True
    trace = decision["raw_decision"]["router_trace"]
    assert trace["semantic_route"] == "direct_answer"
    assert "llm_downgrade_blocked_by_write_fallback" in trace["normalization_notes"]


def test_semantic_clarification_cannot_downgrade_small_edit(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "clarification_needed",
            "complexity": "simple",
            "confidence": 0.7,
            "requires_workspace_read": False,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead"],
            "rationale": "模型认为需要更多上下文。",
            "intent": "semantic_clarification",
            "source": "semantic_intent_classifier",
            "raw_semantic_result": {"route": "clarification_needed"},
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("帮我补充函数注释"))

    assert decision["route"] == "small_edit"
    assert decision["complexity"] == "small_code"
    assert decision["requires_workspace_write"] is True
    assert "llm_downgrade_blocked_by_write_fallback" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_read_only_cannot_downgrade_test_only(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "read_only",
            "complexity": "simple",
            "confidence": 0.9,
            "requires_workspace_read": True,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead"],
            "rationale": "模型漏掉了需要运行测试。",
            "intent": "semantic_read_only",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("check the test suite"))

    assert decision["route"] == "test_only"
    assert decision["requires_workspace_read"] is True
    assert decision["requires_shell"] is True
    assert "llm_downgrade_blocked_by_test_fallback" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_direct_answer_cannot_downgrade_workspace_read(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "direct_answer",
            "complexity": "simple",
            "confidence": 0.9,
            "requires_workspace_read": False,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead"],
            "rationale": "模型漏掉了项目读取。",
            "intent": "semantic_direct",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("看看项目设置都有哪些"))

    assert decision["route"] == "read_only"
    assert decision["requires_workspace_read"] is True
    assert "llm_downgrade_blocked_by_read_fallback" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_read_only_cannot_downgrade_review_only(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "read_only",
            "complexity": "simple",
            "confidence": 0.9,
            "requires_workspace_read": True,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead"],
            "rationale": "模型漏掉了复核语义。",
            "intent": "semantic_read",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("复核交付报告有没有遗漏"))

    assert decision["route"] == "review_only"
    assert decision["requires_workspace_read"] is True
    assert "llm_downgrade_blocked_by_review_fallback" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_write_upgrade_blocked_for_read_only_review(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "small_edit",
            "complexity": "small_code",
            "confidence": 0.97,
            "requires_workspace_read": True,
            "requires_workspace_write": True,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead", "Coder"],
            "rationale": "模型误以为需要修改文件。",
            "intent": "semantic_overeager_small_edit",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(
        classify_user_intent_async("你帮我看看这个路径下有没有算法代码，代码写的对不对")
    )

    assert decision["route"] == "review_only"
    assert decision["requires_workspace_read"] is True
    assert decision["requires_workspace_write"] is False
    assert "llm_write_upgrade_blocked_by_read_fallback" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_shadow_keeps_legacy_decision_but_records_trace(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "direct_answer",
            "complexity": "simple",
            "confidence": 0.99,
            "requires_workspace_read": False,
            "requires_workspace_write": False,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead"],
            "rationale": "shadow only",
            "intent": "semantic_shadow",
            "source": "semantic_intent_classifier",
        }

    async def fake_legacy(prompt, *, conversation_summary="", fallback=None):
        return {
            "route": "feature_delivery",
            "complexity": "medium",
            "confidence": 0.9,
            "requires_workspace_read": True,
            "requires_workspace_write": True,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead", "Coder"],
            "rationale": "legacy path",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "shadow")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)
    monkeypatch.setattr("src.api.services.intent_llm_classifier.classify_intent_v3_with_llm", fake_legacy)

    decision = asyncio.run(classify_user_intent_async("请实现一个导入模块"))

    assert decision["route"] == "feature_delivery"
    assert decision["raw_decision"]["router_trace"]["semantic_route"] == "direct_answer"
    assert "semantic_mode=shadow" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_low_confidence_write_request_keeps_strong_code_fallback(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "feature_delivery",
            "complexity": "medium",
            "confidence": 0.61,
            "requires_workspace_read": True,
            "requires_workspace_write": True,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead", "Coder"],
            "missing_information": ["需要明确要修改的文件。"],
            "rationale": "目标还不够明确。",
            "intent": "semantic_unclear_code_task",
            "source": "semantic_intent_classifier",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MIN_CONFIDENCE", "0.7")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("请实现一个导入模块并补测试"))

    assert decision["route"] == "feature_delivery"
    assert decision["requires_workspace_write"] is True
    assert decision["requires_workspace_read"] is True
    assert "semantic_low_confidence_clarification" in decision["raw_decision"]["router_trace"]["normalization_notes"]
    assert "llm_downgrade_blocked_by_write_fallback" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_strict_clarifies_when_classifier_unavailable(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return None

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "strict")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("帮我实现一个导入模块"))

    assert decision["route"] == "clarification_needed"
    assert decision["execution_route"] == "lead_direct_reply"
    assert "semantic_unavailable_clarification" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_enabled_can_require_llm_before_execution(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return None

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_REQUIRE_LLM", "true")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)

    decision = asyncio.run(classify_user_intent_async("帮我实现一个导入模块"))

    assert semantic_require_llm() is True
    assert decision["route"] == "clarification_needed"
    assert decision["requires_workspace_write"] is False
    assert "semantic_required_unavailable" in decision["raw_decision"]["router_trace"]["normalization_notes"]


def test_semantic_low_confidence_clarify_can_fall_back_to_legacy(monkeypatch):
    async def fake_semantic(prompt, *, runtime_context=None, fallback=None):
        return {
            "route": "feature_delivery",
            "complexity": "medium",
            "confidence": 0.61,
            "requires_workspace_read": True,
            "requires_workspace_write": True,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead", "Coder"],
            "missing_information": ["需要明确要修改的文件。"],
            "rationale": "目标还不够明确。",
            "intent": "semantic_unclear_code_task",
            "source": "semantic_intent_classifier",
        }

    async def fake_legacy(prompt, *, conversation_summary="", fallback=None):
        return {
            "route": "feature_delivery",
            "complexity": "medium",
            "confidence": 0.9,
            "requires_workspace_read": True,
            "requires_workspace_write": True,
            "requires_shell": False,
            "requires_approval": False,
            "suggested_agents": ["Lead", "Coder"],
            "rationale": "legacy path",
        }

    clear_semantic_intent_cache()
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_MODE", "enabled")
    monkeypatch.setenv("NANOCURSOR_SEMANTIC_INTENT_LOW_CONFIDENCE_CLARIFY", "false")
    monkeypatch.setattr("src.api.services.semantic_intent_classifier.classify_semantic_intent", fake_semantic)
    monkeypatch.setattr("src.api.services.intent_llm_classifier.classify_intent_v3_with_llm", fake_legacy)

    decision = asyncio.run(classify_user_intent_async("请实现一个导入模块并补测试"))

    assert semantic_low_confidence_clarify() is False
    assert decision["route"] == "feature_delivery"
    assert decision["requires_workspace_write"] is True
    assert "semantic_low_confidence_clarification" not in decision["raw_decision"]["router_trace"]["normalization_notes"]
