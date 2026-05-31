"""User intent routing for nanoCursor runs.

The router is deliberately deterministic: it produces an explainable first-pass
classification that the backend can use before any LLM call.  The goal is not to
understand every natural-language nuance, but to avoid unsafe defaults such as
treating short code-generation requests as casual chat.
"""

from __future__ import annotations

import re
from typing import Any


EXECUTION_VERBS = frozenset({
    "帮我", "写", "做", "创建", "生成", "实现", "修改", "修复", "新增", "删除", "重构",
    "运行", "测试", "补充", "构建", "开发", "完成", "比较", "统计", "优化",
    "write", "create", "generate", "implement", "modify", "fix", "add", "delete",
    "refactor", "run", "test", "build", "compare", "benchmark", "optimize",
})

CODE_ARTIFACTS = frozenset({
    "代码", "脚本", "函数", "类", "模块", "接口", "api", "页面", "组件", "样式", "测试",
    "文件", "readme", "配置", "算法", "排序", "性能", "benchmark", "demo", "app",
    "python", "javascript", "typescript", "react", "vue", "fastapi", "sql", "docker",
    "script", "function", "class", "module", "endpoint", "component", "test", "config",
})

READ_ONLY_MARKERS = frozenset({
    "解释", "说明", "是什么", "为什么", "怎么看", "评价", "总结", "分析一下", "讲讲",
    "explain", "why", "what is", "summarize", "review only",
})

GREETING_MARKERS = frozenset({
    "你好", "hello", "hi", "你是谁", "是什么模型", "你能做什么", "关于你",
})

MEDIUM_MARKERS = frozenset({
    "完整", "模块", "多文件", "前后端", "端到端", "流程", "系统", "产品级", "重构",
    "complete", "multi-file", "end-to-end", "system", "architecture",
})

HIGH_RISK_MARKERS = frozenset({
    "安全", "权限", "认证", "鉴权", "secret", "token", "迁移", "数据库", "schema",
    "删除", "批量", "部署", "生产", "上线", "git push", "rm -rf",
})

TOOLING_MARKERS = frozenset({
    "运行", "测试", "验证", "benchmark", "性能", "pytest", "npm test", "check", "lint",
    "run", "test", "verify", "benchmark",
})

REVIEW_MARKERS = frozenset({
    "复核", "审查", "评审", "风险", "diff", "review", "audit", "quality", "regression",
})

EXTERNAL_CONTEXT_MARKERS = frozenset({
    "github", "issue", "issues", "pr", "pull request", "ci", "仓库", "代码审查",
})


def classify_user_intent(prompt: str, *, conversation_summary: str = "") -> dict[str, Any]:
    """Return an explainable routing decision for a user prompt."""
    raw = str(prompt or "").strip()
    text = raw.lower()
    compact = re.sub(r"\s+", "", text)

    if not raw:
        return _decision(
            intent="empty",
            complexity="simple",
            route="lead_direct_reply",
            confidence=1.0,
            rationale="空输入不需要启动执行流程。",
            signals=["empty_prompt"],
        )

    signals: list[str] = []
    if _has_any(text, compact, GREETING_MARKERS):
        signals.append("greeting")
    if _has_any(text, compact, READ_ONLY_MARKERS):
        signals.append("read_only_question")
    if _has_any(text, compact, EXECUTION_VERBS):
        signals.append("execution_verb")
    if _has_any(text, compact, CODE_ARTIFACTS):
        signals.append("code_artifact")
    if _has_any(text, compact, TOOLING_MARKERS):
        signals.append("tooling_or_verification")
    if _has_any(text, compact, REVIEW_MARKERS):
        signals.append("review_or_risk_check")
    if _has_any(text, compact, EXTERNAL_CONTEXT_MARKERS):
        signals.append("external_context")
    if _has_any(text, compact, MEDIUM_MARKERS):
        signals.append("multi_stage_scope")
    if _has_any(text, compact, HIGH_RISK_MARKERS):
        signals.append("high_risk_scope")
    if len(raw) > 260:
        signals.append("long_prompt")
    if conversation_summary and any(word in conversation_summary.lower() for word in ["代码", "项目", "run", "diff"]):
        signals.append("coding_conversation_context")

    wants_artifact = "execution_verb" in signals and "code_artifact" in signals
    explicit_code = "code_artifact" in signals and not _pure_read_only(signals)
    requires_write = wants_artifact or explicit_code
    requires_execution = "tooling_or_verification" in signals

    if "high_risk_scope" in signals:
        return _decision(
            intent="high_risk_change",
            complexity="high_risk",
            route="agenthub_delivery",
            requires_workspace_write=True,
            requires_execution=requires_execution,
            confidence=0.9,
            rationale="请求包含安全、迁移、部署、删除或生产风险，需要进入高风险执行与复核流程。",
            signals=signals,
        )

    if "external_context" in signals:
        return _decision(
            intent="external_context_lookup",
            complexity="simple",
            route="agenthub_delivery",
            requires_workspace_write=False,
            requires_execution=True,
            confidence=0.78,
            rationale="请求涉及 GitHub、Issue、PR 或 CI 等外部研发上下文，需要进入工具/MCP 能力流程。",
            signals=signals,
        )

    if "review_or_risk_check" in signals and "execution_verb" in signals:
        return _decision(
            intent="review_or_risk_check",
            complexity="simple",
            route="agenthub_delivery",
            requires_workspace_write=False,
            requires_execution=True,
            confidence=0.76,
            rationale="请求要求复核、审查或风险检查，应进入可追踪执行流程并产出复核证据。",
            signals=signals,
        )

    if requires_write:
        complexity = "medium" if {"multi_stage_scope", "long_prompt"} & set(signals) else "small_code"
        intent = "code_generation" if wants_artifact else "code_task"
        return _decision(
            intent=intent,
            complexity=complexity,
            route="agenthub_delivery",
            requires_workspace_write=True,
            requires_execution=requires_execution,
            confidence=0.84 if wants_artifact else 0.72,
            rationale="请求包含执行动词和代码/脚本/算法等产物信号，应进入代码执行流程而不是 Lead 直答。",
            signals=signals,
        )

    if "read_only_question" in signals and "greeting" not in signals:
        return _decision(
            intent="explanation",
            complexity="simple",
            route="lead_direct_reply",
            confidence=0.78,
            rationale="请求更像解释、总结或建议，不需要默认修改工作区。",
            signals=signals,
        )

    if "greeting" in signals and len(raw) <= 160:
        return _decision(
            intent="greeting",
            complexity="simple",
            route="lead_direct_reply",
            confidence=0.86,
            rationale="轻量问候或自我介绍问题，由 Lead 直接回复。",
            signals=signals,
        )

    if "multi_stage_scope" in signals or "long_prompt" in signals:
        return _decision(
            intent="analysis_or_planning",
            complexity="medium",
            route="agenthub_delivery",
            confidence=0.62,
            rationale="请求范围较大但缺少明确产物，进入轻量规划执行以避免过早结束。",
            signals=signals,
        )

    return _decision(
        intent="general_chat",
        complexity="simple",
        route="lead_direct_reply",
        confidence=0.58,
        rationale="未识别出明确代码产物或高风险执行信号，按 Lead 直接回复处理。",
        signals=signals or ["no_strong_signal"],
    )


def is_lead_direct_intent(prompt: str, *, conversation_summary: str = "") -> bool:
    """Whether the prompt should skip the full delivery pipeline."""
    return classify_user_intent(prompt, conversation_summary=conversation_summary)["route"] == "lead_direct_reply"


async def classify_user_intent_async(
    prompt: str,
    *,
    conversation_summary: str = "",
) -> dict[str, Any]:
    """Classify intent with LLM assistance, falling back to keywords."""
    raw = str(prompt or "").strip()
    if not raw:
        return _decision(
            intent="empty", complexity="simple", route="lead_direct_reply",
            confidence=1.0, rationale="空输入不需要启动执行流程。", signals=["empty_prompt"],
        )

    from src.agent.strategy.classifier import classify_with_llm

    llm_result = await classify_with_llm(prompt, conversation_summary)
    if llm_result:
        complexity = llm_result["complexity"]
        route = "lead_direct_reply" if complexity == "simple" else "agenthub_delivery"
        return _decision(
            intent="llm_classified",
            complexity=complexity,
            route=route,
            confidence=llm_result["confidence"],
            rationale=llm_result.get("rationale", "LLM 分类"),
            signals=["llm_classified"] + [f"role:{r}" for r in llm_result.get("needed_roles", [])],
        )

    # Fallback to keyword-based classification
    return classify_user_intent(prompt, conversation_summary=conversation_summary)


def _has_any(text: str, compact: str, markers: frozenset[str]) -> bool:
    for marker in markers:
        lowered = marker.lower()
        if " " in lowered:
            if lowered in text:
                return True
        elif lowered in compact or lowered in text:
            return True
    return False


def _pure_read_only(signals: list[str]) -> bool:
    signal_set = set(signals)
    return "read_only_question" in signal_set and "execution_verb" not in signal_set


def _decision(
    *,
    intent: str,
    complexity: str,
    route: str,
    confidence: float,
    rationale: str,
    signals: list[str],
    requires_workspace_write: bool = False,
    requires_execution: bool = False,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "level": complexity,
        "complexity": complexity,
        "route": route,
        "requires_workspace_write": requires_workspace_write,
        "requires_execution": requires_execution,
        "confidence": round(float(confidence), 2),
        "rationale": rationale,
        "signals": signals,
        "indicators": signals,
    }
