"""Structured user intent routing for nanoCursor runs.

The router separates product-level intent from runtime execution:

- ``route``: what the user is asking for, such as ``direct_answer`` or
  ``feature_delivery``.
- ``execution_route``: how the current backend should run it, either
  ``lead_direct_reply`` or ``agenthub_delivery``.

This keeps the public decision contract precise without forcing a risky rewrite
of the existing execution pipeline.
"""

from __future__ import annotations

import re
from typing import Any

from src.api.models import IntentDecision


EXECUTION_VERBS = frozenset({
    "帮我", "写", "做", "创建", "生成", "实现", "修改", "修复", "新增", "删除", "重构",
    "运行", "测试", "补充", "构建", "开发", "完成", "比较", "统计", "优化", "改",
    "write", "create", "generate", "implement", "modify", "fix", "add", "delete",
    "refactor", "run", "test", "build", "compare", "benchmark", "optimize",
})

WRITE_ACTION_MARKERS = frozenset({
    "写", "创建", "生成", "实现", "修改", "修复", "新增", "删除", "重构", "补充",
    "构建", "开发", "完成", "优化", "改",
    "write", "create", "generate", "implement", "modify", "fix", "add", "delete",
    "refactor", "build", "optimize",
})

CODE_ARTIFACTS = frozenset({
    "代码", "脚本", "函数", "类", "模块", "接口", "api", "页面", "组件", "样式", "测试",
    "文件", "readme", "配置", "算法", "排序", "性能", "benchmark", "demo", "app",
    "python", "javascript", "typescript", "react", "vue", "fastapi", "sql", "docker",
    "script", "function", "class", "module", "endpoint", "component", "test", "config",
})

DIRECT_ANSWER_MARKERS = frozenset({
    "解释", "说明", "是什么", "为什么", "怎么看", "评价", "总结", "讲讲",
    "explain", "why", "what is", "summarize",
})

WORKSPACE_READ_MARKERS = frozenset({
    "看看", "看一下", "看一看", "检查", "分析一下", "项目结构", "目录结构", "文件结构",
    "这个项目", "这个仓库", "这个文件夹", "当前目录", "有哪些文件", "都有什么",
    "inspect", "scan", "analyze", "project structure", "repository", "folder",
})

GREETING_MARKERS = frozenset({
    "你好", "您好", "哈喽", "哈啰", "嗨", "hello", "hi", "hey",
    "你是谁", "是什么模型", "你能做什么", "关于你",
})

SMALL_EDIT_MARKERS = frozenset({
    "错别字", "typo", "拼写", "文案", "一行", "小改", "微调", "readme", "注释",
    "rename", "format", "lint fix",
})

MEDIUM_MARKERS = frozenset({
    "完整", "模块", "多文件", "前后端", "端到端", "流程", "系统", "产品级", "重构",
    "complete", "multi-file", "end-to-end", "system", "architecture",
})

HIGH_RISK_MARKERS = frozenset({
    "安全", "权限", "认证", "鉴权", "secret", "token", "迁移", "数据库", "schema",
    "删除", "批量", "部署", "生产", "上线", "git push", "rm -rf", "node_modules",
    "install", "安装依赖", "网络请求",
})

TOOLING_MARKERS = frozenset({
    "运行", "测试", "验证", "benchmark", "性能", "pytest", "npm test", "check", "lint",
    "run", "test", "verify", "benchmark",
})

REVIEW_MARKERS = frozenset({
    "复核", "审查", "评审", "风险", "diff", "review", "audit", "quality", "regression",
})

DEBUG_MARKERS = frozenset({
    "bug", "报错", "错误", "异常", "失败", "崩溃", "404", "500", "traceback",
    "fix bug", "broken", "error", "exception", "crash",
})

EXTERNAL_CONTEXT_MARKERS = frozenset({
    "github", "issue", "issues", "pr", "pull request", "ci", "仓库", "代码审查",
})

AMBIGUOUS_ACTION_MARKERS = frozenset({
    "优化一下", "改一下", "弄一下", "搞一下", "完善一下", "处理一下",
    "make it better", "improve it", "fix it",
})

FOLLOWUP_MARKERS = frozenset({
    "继续", "接着", "按刚才", "按照刚才", "按上面", "按照上面", "继续做", "继续改",
    "继续实现", "继续修", "下一步", "接下来", "照着刚才",
    "continue", "keep going", "next step", "as above", "same way",
})


def classify_user_intent(prompt: str, *, conversation_summary: str = "") -> dict[str, Any]:
    """Return a structured, explainable routing decision for a user prompt."""
    guarded = _classify_deterministic(prompt, conversation_summary=conversation_summary)
    from src.api.services.intent_guards import evaluate_intent_guards
    from src.api.services.intent_normalizer import normalize_intent_decision

    guards = evaluate_intent_guards(prompt, guarded)
    return normalize_intent_decision(None, fallback=guarded, guards=guards).model_dump()


def is_lead_direct_intent(prompt: str, *, conversation_summary: str = "") -> bool:
    """Whether the prompt should skip the full delivery pipeline."""
    decision = _classify_deterministic(prompt, conversation_summary=conversation_summary)
    return decision.execution_route == "lead_direct_reply"


async def classify_user_intent_async(
    prompt: str,
    *,
    conversation_summary: str = "",
) -> dict[str, Any]:
    """Classify intent with deterministic guards plus optional LLM assistance.

    High-confidence guards always win for empty, direct answer, risky, and
    clarification cases. For ordinary coding requests, the existing lightweight
    LLM classifier can refine strategy/roles, but its result is normalized back
    into ``IntentDecision``.
    """
    guarded = _classify_deterministic(prompt, conversation_summary=conversation_summary)
    from src.api.services.intent_guards import evaluate_intent_guards
    from src.api.services.intent_llm_classifier import classify_intent_v3_with_llm
    from src.api.services.intent_normalizer import normalize_intent_decision

    guards = evaluate_intent_guards(prompt, guarded)
    if guards.hard_decision is not None:
        return normalize_intent_decision(None, fallback=guarded, guards=guards).model_dump()

    raw_decision = await classify_intent_v3_with_llm(
        prompt,
        conversation_summary=conversation_summary,
        fallback=guarded,
    )
    normalized = normalize_intent_decision(raw_decision, fallback=guarded, guards=guards)
    if raw_decision and normalized.confidence < guarded.confidence:
        return normalize_intent_decision(None, fallback=guarded, guards=guards).model_dump()
    return normalized.model_dump()


def _classify_deterministic(prompt: str, *, conversation_summary: str = "") -> IntentDecision:
    raw = str(prompt or "").strip()
    text = raw.lower()
    compact = re.sub(r"\s+", "", text)

    if not raw:
        return _decision(
            route="clarification_needed",
            intent="empty",
            level="simple",
            strategy="analysis_only",
            confidence=1.0,
            rationale="空输入没有可执行目标，需要用户补充需求。",
            signals=["empty_prompt"],
            missing_information=["请输入你希望 nanoCursor 完成的问题或代码任务。"],
        )

    signals = _collect_signals(raw, text, compact, conversation_summary)

    if "high_risk_scope" in signals:
        return _decision(
            route="risky_operation",
            intent="high_risk_change",
            level="high_risk",
            strategy="feature_delivery",
            confidence=0.92,
            rationale="请求包含删除、安装依赖、数据库、权限、部署或生产风险，必须进入高风险流程并等待审批。",
            signals=signals,
            requires_workspace_read=True,
            requires_workspace_write=True,
            requires_shell=True,
            requires_approval=True,
            suggested_agents=["Lead", "Planner", "Coder", "Reviewer", "Tester", "Security"],
        )

    if _is_greeting_or_identity(raw, signals):
        return _decision(
            route="direct_answer",
            intent="greeting",
            level="simple",
            strategy="analysis_only",
            confidence=0.9,
            rationale="轻量问候、身份或能力询问，由 Lead 直接回答，不创建任务卡。",
            signals=signals,
            suggested_agents=["Lead"],
        )

    if _is_ambiguous_action(raw, signals):
        return _decision(
            route="clarification_needed",
            intent="ambiguous_action",
            level="simple",
            strategy="analysis_only",
            confidence=0.72,
            rationale="请求包含动作倾向但缺少明确对象、范围或验收标准，直接执行容易误改项目。",
            signals=signals,
            requires_workspace_read="workspace_read" in signals,
            missing_information=["需要明确要优化或修改的对象、范围和期望结果。"],
            suggested_agents=["Lead"],
        )

    if "conversation_followup" in signals and "coding_conversation_context" in signals:
        route, level, strategy, requires_write, requires_shell, agents, rationale = _followup_decision_from_context(
            conversation_summary
        )
        return _decision(
            route=route,
            intent="conversation_followup",
            level=level,
            strategy=strategy,
            confidence=0.76,
            rationale=rationale,
            signals=signals,
            requires_workspace_read=True,
            requires_workspace_write=requires_write,
            requires_shell=requires_shell,
            suggested_agents=agents,
        )

    if "debug_signal" in signals:
        return _decision(
            route="debug_fix",
            intent="debug_fix",
            level="medium" if "multi_stage_scope" in signals else "small_code",
            strategy="bug_fix",
            confidence=0.84,
            rationale="请求包含报错、异常或失败信号，需要进入可验证的修复流程。",
            signals=signals,
            requires_workspace_read=True,
            requires_workspace_write=True,
            requires_shell=True,
            suggested_agents=["Lead", "Coder", "Tester", "Reviewer"],
        )

    if "review_or_risk_check" in signals:
        return _decision(
            route="review_only",
            intent="review_or_risk_check",
            level="simple",
            strategy="analysis_only",
            confidence=0.8,
            rationale="请求要求复核、审查或风险检查，应该读取项目证据但默认不写文件。",
            signals=signals,
            requires_workspace_read=True,
            requires_shell="tooling_or_verification" in signals,
            suggested_agents=["Lead", "Reviewer"],
        )

    if "tooling_or_verification" in signals and "write_action" not in signals:
        return _decision(
            route="test_only",
            intent="test_or_verification",
            level="small_code",
            strategy="analysis_only",
            confidence=0.78,
            rationale="请求以运行测试、验证或 benchmark 为主，默认不写文件但需要 shell 权限。",
            signals=signals,
            requires_workspace_read=True,
            requires_shell=True,
            suggested_agents=["Lead", "Tester"],
        )

    wants_artifact = "write_action" in signals and "code_artifact" in signals
    explicit_code = "code_artifact" in signals and "direct_answer_question" not in signals
    if wants_artifact or explicit_code:
        route = "small_edit" if _looks_like_small_edit(signals) else "feature_delivery"
        level = "small_code" if route == "small_edit" else "medium"
        if "multi_stage_scope" not in signals and route == "feature_delivery":
            level = "small_code"
        return _decision(
            route=route,
            intent="code_generation" if wants_artifact else "code_task",
            level=level,
            strategy="small_patch" if route == "small_edit" else "feature_delivery",
            confidence=0.86 if wants_artifact else 0.76,
            rationale="请求包含执行动词和代码/脚本/算法等产物信号，应进入代码执行流程而不是 Lead 直答。",
            signals=signals,
            requires_workspace_read=True,
            requires_workspace_write=True,
            requires_shell="tooling_or_verification" in signals,
            suggested_agents=_agents_for_level(level),
        )

    if "workspace_read" in signals or "external_context" in signals:
        return _decision(
            route="read_only",
            intent="workspace_inspection",
            level="simple",
            strategy="analysis_only",
            confidence=0.78,
            rationale="请求需要查看项目、目录、文件、PR 或 CI 上下文，但没有明确写入要求。",
            signals=signals,
            requires_workspace_read=True,
            requires_shell="external_context" in signals,
            suggested_agents=["Lead", "Reviewer"] if "external_context" in signals else ["Lead"],
        )

    if "direct_answer_question" in signals:
        return _decision(
            route="direct_answer",
            intent="explanation",
            level="simple",
            strategy="analysis_only",
            confidence=0.8,
            rationale="请求更像通用解释、总结或建议，不需要默认读取或修改工作区。",
            signals=signals,
            suggested_agents=["Lead"],
        )

    if "multi_stage_scope" in signals or "long_prompt" in signals:
        return _decision(
            route="clarification_needed",
            intent="broad_or_unclear_scope",
            level="simple",
            strategy="analysis_only",
            confidence=0.62,
            rationale="请求范围较大但缺少明确产物，先要求澄清比直接执行更安全。",
            signals=signals,
            missing_information=["需要明确目标产物、涉及文件或验收标准。"],
            suggested_agents=["Lead"],
        )

    return _decision(
        route="direct_answer",
        intent="general_chat",
        level="simple",
        strategy="analysis_only",
        confidence=0.62,
        rationale="未识别出明确项目读取、代码产物或高风险执行信号，按 Lead 直接回复处理。",
        signals=signals or ["no_strong_signal"],
        suggested_agents=["Lead"],
    )


def _decision_from_llm(llm_result: dict[str, Any], guarded: IntentDecision) -> IntentDecision:
    strategy = str(llm_result.get("strategy") or guarded.strategy)
    complexity = str(llm_result.get("complexity") or guarded.level)
    confidence = float(llm_result.get("confidence") or 0)
    roles = [str(role).strip().title() for role in llm_result.get("needed_roles", []) if str(role).strip()]
    if "Lead" not in roles:
        roles.insert(0, "Lead")

    route_by_strategy = {
        "analysis_only": "read_only" if guarded.requires_workspace_read else "direct_answer",
        "docs_only": "small_edit",
        "small_patch": "small_edit",
        "bug_fix": "debug_fix",
        "refactor": "feature_delivery",
        "feature_delivery": "feature_delivery",
    }
    route = route_by_strategy.get(strategy, guarded.route)
    if complexity == "simple" and not guarded.requires_workspace_read and route == "read_only":
        route = "direct_answer"
    if complexity == "high_risk":
        route = "risky_operation"

    return _decision(
        route=route,
        intent=f"llm_{strategy}",
        level=complexity,
        strategy=strategy,
        confidence=confidence,
        rationale=str(llm_result.get("rationale") or guarded.rationale),
        signals=["llm_classified", *guarded.signals],
        requires_workspace_read=route != "direct_answer",
        requires_workspace_write=route in {"small_edit", "feature_delivery", "debug_fix", "risky_operation"},
        requires_shell=guarded.requires_shell or route in {"debug_fix", "test_only", "risky_operation"},
        requires_execution=route not in {"direct_answer", "clarification_needed"},
        requires_approval=route == "risky_operation" or guarded.requires_approval,
        suggested_agents=roles or guarded.suggested_agents,
        source="llm_structured_intent",
    )


def _collect_signals(raw: str, text: str, compact: str, conversation_summary: str) -> list[str]:
    signals: list[str] = []
    if _has_any(text, compact, GREETING_MARKERS):
        signals.append("greeting")
    if _has_any(text, compact, DIRECT_ANSWER_MARKERS):
        signals.append("direct_answer_question")
    if _has_any(text, compact, WORKSPACE_READ_MARKERS):
        signals.append("workspace_read")
    if _has_any(text, compact, EXECUTION_VERBS):
        signals.append("execution_verb")
    if _has_any(text, compact, WRITE_ACTION_MARKERS):
        signals.append("write_action")
    if _has_any(text, compact, CODE_ARTIFACTS):
        signals.append("code_artifact")
    if _has_any(text, compact, SMALL_EDIT_MARKERS):
        signals.append("small_edit_signal")
    if _has_any(text, compact, TOOLING_MARKERS):
        signals.append("tooling_or_verification")
    if _has_any(text, compact, REVIEW_MARKERS):
        signals.append("review_or_risk_check")
    if _has_any(text, compact, DEBUG_MARKERS):
        signals.append("debug_signal")
    if _has_any(text, compact, EXTERNAL_CONTEXT_MARKERS):
        signals.append("external_context")
    if _has_any(text, compact, MEDIUM_MARKERS):
        signals.append("multi_stage_scope")
    if _has_any(text, compact, HIGH_RISK_MARKERS):
        signals.append("high_risk_scope")
    if _has_any(text, compact, AMBIGUOUS_ACTION_MARKERS):
        signals.append("ambiguous_action")
    if _has_any(text, compact, FOLLOWUP_MARKERS):
        signals.append("conversation_followup")
    if len(raw) > 260:
        signals.append("long_prompt")
    if conversation_summary and any(
        word in conversation_summary.lower()
        for word in ["代码", "项目", "run", "diff", "测试", "验证", "pytest", "报错", "错误", "文件", "实现"]
    ):
        signals.append("coding_conversation_context")
    return signals


def _followup_decision_from_context(
    conversation_summary: str,
) -> tuple[str, str, str, bool, bool, list[str], str]:
    """Infer the minimal route for a short follow-up from conversation memory."""
    text = str(conversation_summary or "").lower()
    if any(marker in text for marker in ["报错", "错误", "失败", "traceback", "bug", "exception"]):
        return (
            "debug_fix",
            "medium",
            "bug_fix",
            True,
            True,
            ["Lead", "Coder", "Tester", "Reviewer"],
            "用户使用连续对话跟进上一轮失败/报错上下文，应继续修复并验证。",
        )
    if any(marker in text for marker in ["pytest", "测试", "验证", "test", "benchmark"]):
        return (
            "test_only",
            "small_code",
            "analysis_only",
            False,
            True,
            ["Lead", "Tester"],
            "用户使用连续对话跟进上一轮验证上下文，应继续执行只读/测试路径。",
        )
    if any(marker in text for marker in ["diff", "审查", "review", "风险", "复核"]):
        return (
            "review_only",
            "simple",
            "analysis_only",
            False,
            False,
            ["Lead", "Reviewer"],
            "用户使用连续对话跟进上一轮审查上下文，应继续只读复核。",
        )
    if any(marker in text for marker in ["只读", "查看", "看看", "分析", "read_only", "inspect"]):
        return (
            "read_only",
            "simple",
            "analysis_only",
            False,
            False,
            ["Lead"],
            "用户使用连续对话跟进上一轮项目阅读上下文，应继续只读分析。",
        )
    return (
        "feature_delivery",
        "small_code",
        "feature_delivery",
        True,
        False,
        ["Lead", "Coder"],
        "用户使用连续对话跟进上一轮代码任务，应参考会话摘要继续执行而不是当作闲聊。",
    )


def _decision(
    *,
    route: str,
    intent: str,
    level: str,
    strategy: str,
    confidence: float,
    rationale: str,
    signals: list[str],
    requires_workspace_read: bool = False,
    requires_workspace_write: bool = False,
    requires_shell: bool = False,
    requires_execution: bool | None = None,
    requires_approval: bool = False,
    suggested_agents: list[str] | None = None,
    missing_information: list[str] | None = None,
    source: str = "deterministic_guard",
) -> IntentDecision:
    execution_route = "lead_direct_reply" if route in {"direct_answer", "clarification_needed"} else "agenthub_delivery"
    if requires_execution is None:
        requires_execution = execution_route == "agenthub_delivery"
    agents = _unique_agents(suggested_agents or ["Lead"])
    return IntentDecision(
        route=route,  # type: ignore[arg-type]
        confidence=round(float(confidence), 2),
        requires_workspace_read=requires_workspace_read,
        requires_workspace_write=requires_workspace_write,
        requires_shell=requires_shell,
        requires_execution=requires_execution,
        requires_approval=requires_approval,
        suggested_agents=agents,
        rationale=rationale,
        missing_information=missing_information or [],
        intent=intent,
        level=level,
        complexity=level,
        strategy=strategy,
        execution_route=execution_route,  # type: ignore[arg-type]
        signals=signals,
        indicators=signals,
        source=source,
    )


def _has_any(text: str, compact: str, markers: frozenset[str]) -> bool:
    for marker in markers:
        lowered = marker.lower()
        if " " in lowered:
            if lowered in text:
                return True
        elif lowered in compact or lowered in text:
            return True
    return False


def _is_greeting_or_identity(raw: str, signals: list[str]) -> bool:
    return "greeting" in signals and len(raw) <= 180 and not {"execution_verb", "code_artifact"} & set(signals)


def _is_ambiguous_action(raw: str, signals: list[str]) -> bool:
    signal_set = set(signals)
    if "ambiguous_action" not in signal_set:
        return False
    if {"code_artifact", "workspace_read", "debug_signal", "review_or_risk_check"} & signal_set:
        return False
    return len(raw) <= 120


def _looks_like_small_edit(signals: list[str]) -> bool:
    signal_set = set(signals)
    return "small_edit_signal" in signal_set


def _agents_for_level(level: str) -> list[str]:
    if level == "high_risk":
        return ["Lead", "Planner", "Coder", "Reviewer", "Tester"]
    if level == "medium":
        return ["Lead", "Planner", "Coder", "Reviewer"]
    if level == "small_code":
        return ["Lead", "Coder"]
    return ["Lead"]


def _unique_agents(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        name = str(item or "").strip()
        if not name:
            continue
        name = name[:1].upper() + name[1:]
        if name.lower() not in {existing.lower() for existing in result}:
            result.append(name)
    return result or ["Lead"]
