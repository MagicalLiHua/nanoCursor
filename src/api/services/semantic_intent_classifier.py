"""Semantic intent classifier for Intent Router V3.

This module is deliberately optional. Hard guards and the deterministic router
remain the fallback path. When enabled, the classifier asks the model for a
structured route decision using prompt + compact runtime context, then returns a
plain dictionary that still has to pass through ``intent_normalizer``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.api.models import IntentDecision, IntentRoute
from src.api.services.intent_runtime_context import (
    IntentRuntimeContext,
    coerce_intent_runtime_context,
)

logger = logging.getLogger(__name__)


VALID_ROUTES = set(IntentRoute.__args__)  # type: ignore[attr-defined]
VALID_COMPLEXITIES = {"simple", "small_code", "medium", "high_risk"}
VALID_RISK_LEVELS = {"low", "medium", "high"}


class SemanticIntentRawDecision(BaseModel):
    """Structured model output before backend policy normalization."""

    route: IntentRoute
    confidence: float = Field(ge=0.0, le=1.0)
    complexity: str = "simple"
    intent_summary: str = ""
    user_goal: str = ""
    needs_workspace_read: bool = False
    needs_workspace_write: bool = False
    needs_shell: bool = False
    needs_approval: bool = False
    risk_level: str = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    suggested_agents: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    reasoning: str = ""


class IntentRouterTrace(BaseModel):
    """Debug trace for one routing decision."""

    prompt_hash: str
    deterministic_signals: list[str] = Field(default_factory=list)
    deterministic_hints: list[str] = Field(default_factory=list)
    guard_hits: list[str] = Field(default_factory=list)
    semantic_used: bool = False
    semantic_confidence: float | None = None
    semantic_route: str | None = None
    fallback_route: str = ""
    final_route: str = ""
    normalization_notes: list[str] = Field(default_factory=list)
    runtime_context_used: dict[str, Any] = Field(default_factory=dict)


_CACHE: dict[str, dict[str, Any] | None] = {}

_PROMPT = """你是 nanoCursor 的语义意图路由器。根据用户输入和运行上下文，判断本轮应该如何处理。

可选 route:
- direct_answer: 直接回答，不读取/修改工作区。
- read_only: 需要只读查看项目/文件/外部上下文。
- small_edit: 局部小改动，例如 README、注释、typo、单文件小 patch。
- feature_delivery: 需要生成或修改代码并交付结果。
- debug_fix: 需要定位并修复报错、测试失败或异常。
- test_only: 只运行测试、lint、benchmark 或验证命令，不写文件。
- review_only: 审查 diff、风险、代码质量，默认不写文件。
- risky_operation: 删除、安装依赖、git push、数据库迁移、secret、安全权限等高风险任务。
- clarification_needed: 目标、对象或验收标准不清楚，执行前需要澄清。

判断原则:
1. 如果用户明确说不要修改文件，needs_workspace_write 必须是 false。
2. 如果用户只是询问思路、解释、评价，优先 direct_answer 或 read_only。
3. 如果用户要求看当前项目/目录/文件，使用 read_only。
4. 如果用户要求写代码、修 bug、补测试，选择对应代码 route。
5. 高风险动作必须 risk_level=high 且 needs_approval=true。
6. 如果无法确定要改什么，选择 clarification_needed。

用户输入:
{prompt}

运行上下文 JSON:
{runtime_context}

确定性路由提示 JSON:
{deterministic_context}

确定性路由提示不是最终答案，但它包含后端规则识别到的强信号。若其中出现 code_artifact_hint、tooling_hint、debug_hint、small_edit_hint 等信号，除非用户明确说“只解释、不要改代码、不要执行”，不要轻易降级为 direct_answer。

只输出 JSON，不要输出 Markdown。格式:
{{
  "route": "direct_answer",
  "confidence": 0.0,
  "complexity": "simple",
  "intent_summary": "一句话总结意图",
  "user_goal": "用户真正目标",
  "needs_workspace_read": false,
  "needs_workspace_write": false,
  "needs_shell": false,
  "needs_approval": false,
  "risk_level": "low",
  "risk_reasons": [],
  "suggested_agents": ["Lead"],
  "expected_artifacts": [],
  "missing_information": [],
  "reasoning": "简短说明，不要输出长推理"
}}"""


def semantic_intent_mode() -> str:
    """Return disabled/shadow/enabled/strict mode."""

    raw = os.getenv("NANOCURSOR_SEMANTIC_INTENT_MODE")
    if raw is None:
        enabled_raw = os.getenv("NANOCURSOR_SEMANTIC_INTENT_ENABLED", "true").strip().lower()
        return "disabled" if enabled_raw in {"0", "false", "no", "off"} else "enabled"
    mode = raw.strip().lower()
    return mode if mode in {"disabled", "shadow", "enabled", "strict"} else "enabled"


def semantic_intent_trace_enabled() -> bool:
    return os.getenv("NANOCURSOR_SEMANTIC_INTENT_TRACE", "true").lower() in {"1", "true", "yes", "on"}


def semantic_require_llm() -> bool:
    return os.getenv("NANOCURSOR_SEMANTIC_INTENT_REQUIRE_LLM", "false").lower() in {"1", "true", "yes", "on"}


def semantic_low_confidence_clarify() -> bool:
    return os.getenv("NANOCURSOR_SEMANTIC_INTENT_LOW_CONFIDENCE_CLARIFY", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def semantic_min_confidence() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("NANOCURSOR_SEMANTIC_INTENT_MIN_CONFIDENCE", "0.7"))))
    except ValueError:
        return 0.7


def semantic_timeout_seconds() -> float:
    try:
        return max(0.5, min(30.0, float(os.getenv("NANOCURSOR_SEMANTIC_INTENT_TIMEOUT_SECONDS", "6"))))
    except ValueError:
        return 6.0


def build_router_trace(
    *,
    prompt: str,
    fallback: IntentDecision,
    guard_hits: list[str],
    runtime_context: IntentRuntimeContext | dict[str, Any] | None = None,
    semantic_result: dict[str, Any] | None = None,
    final_route: str = "",
    normalization_notes: list[str] | None = None,
) -> IntentRouterTrace:
    """Build a serializable trace for diagnostics."""

    context = coerce_intent_runtime_context(runtime_context)
    signals = list(fallback.signals or fallback.indicators or [])
    return IntentRouterTrace(
        prompt_hash=hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16],
        deterministic_signals=signals,
        deterministic_hints=_signals_to_hints(signals),
        guard_hits=list(guard_hits or []),
        semantic_used=bool(semantic_result),
        semantic_confidence=(
            float(semantic_result["confidence"])
            if semantic_result and isinstance(semantic_result.get("confidence"), (int, float))
            else None
        ),
        semantic_route=str(semantic_result.get("route")) if semantic_result else None,
        fallback_route=str(fallback.route),
        final_route=str(final_route or fallback.route),
        normalization_notes=list(normalization_notes or []),
        runtime_context_used=context.compact_for_prompt(),
    )


async def classify_semantic_intent(
    prompt: str,
    *,
    runtime_context: IntentRuntimeContext | dict[str, Any] | None = None,
    fallback: IntentDecision,
) -> dict[str, Any] | None:
    """Classify a prompt semantically, returning raw normalized input or None."""

    mode = semantic_intent_mode()
    if mode == "disabled":
        return None
    if not str(prompt or "").strip():
        return None

    context = coerce_intent_runtime_context(runtime_context, conversation_summary="")
    cache_key = _cache_key(prompt, context)
    if cache_key in _CACHE:
        cached = _CACHE[cache_key]
        return dict(cached) if isinstance(cached, dict) else None

    try:
        result = await asyncio.wait_for(
            _call_semantic_classifier(prompt, context, fallback),
            timeout=semantic_timeout_seconds(),
        )
    except asyncio.TimeoutError:
        logger.warning("Semantic intent classifier timed out")
        _CACHE[cache_key] = None
        return None
    except Exception as exc:
        logger.warning("Semantic intent classifier failed: %s", exc)
        _CACHE[cache_key] = None
        return None

    if not result:
        _CACHE[cache_key] = None
        return None
    _CACHE[cache_key] = result
    return dict(result)


async def _call_semantic_classifier(
    prompt: str,
    context: IntentRuntimeContext,
    fallback: IntentDecision,
) -> dict[str, Any] | None:
    """Call the configured LLM and parse a semantic intent JSON response."""

    from src.infra.llm_config import create_client, get_model_name

    client = create_client()
    deterministic_context = {
        "route": str(fallback.route),
        "complexity": str(fallback.complexity),
        "confidence": float(fallback.confidence),
        "requires_workspace_read": bool(fallback.requires_workspace_read),
        "requires_workspace_write": bool(fallback.requires_workspace_write),
        "requires_shell": bool(fallback.requires_shell),
        "requires_approval": bool(fallback.requires_approval),
        "risk_level": str(fallback.risk_level),
        "signals": list(fallback.signals or fallback.indicators or []),
        "hints": _signals_to_hints(list(fallback.signals or fallback.indicators or [])),
    }
    message = _PROMPT.format(
        prompt=str(prompt)[:1200],
        runtime_context=json.dumps(context.compact_for_prompt(), ensure_ascii=False),
        deterministic_context=json.dumps(deterministic_context, ensure_ascii=False),
    )
    resp = await client.messages.create(
        model=get_model_name(),
        max_tokens=600,
        temperature=0,
        messages=[{"role": "user", "content": message}],
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text")).strip()
    return parse_semantic_intent_response(text)


def parse_semantic_intent_response(text: str) -> dict[str, Any] | None:
    """Parse and validate semantic intent JSON."""

    cleaned = _strip_fences(str(text or "").strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    try:
        parsed = SemanticIntentRawDecision.model_validate(payload)
    except ValidationError:
        return None
    data = parsed.model_dump(mode="json")
    if data["complexity"] not in VALID_COMPLEXITIES:
        data["complexity"] = "simple"
    if data["risk_level"] not in VALID_RISK_LEVELS:
        data["risk_level"] = "low"
    data["suggested_agents"] = _normalize_roles(data.get("suggested_agents") or [])
    data["requires_workspace_read"] = bool(data.pop("needs_workspace_read", False))
    data["requires_workspace_write"] = bool(data.pop("needs_workspace_write", False))
    data["requires_shell"] = bool(data.pop("needs_shell", False))
    data["requires_approval"] = bool(data.pop("needs_approval", False))
    data["rationale"] = data.pop("reasoning", "") or data.get("intent_summary", "")
    data["intent"] = data.get("intent_summary") or f"semantic_{data['route']}"
    data["source"] = "semantic_intent_classifier"
    data["raw_semantic_result"] = payload
    return data


def clear_semantic_intent_cache() -> None:
    _CACHE.clear()


def _cache_key(prompt: str, context: IntentRuntimeContext) -> str:
    raw = json.dumps(
        {"prompt": str(prompt or "").strip(), "context": context.compact_for_prompt()},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _normalize_roles(raw_roles: list[Any]) -> list[str]:
    roles = [str(role).strip().title() for role in raw_roles if str(role).strip()]
    if "Lead" not in roles:
        roles.insert(0, "Lead")
    result: list[str] = []
    seen: set[str] = set()
    for role in roles:
        key = role.lower()
        if key not in seen:
            result.append(role)
            seen.add(key)
    return result or ["Lead"]


def _signals_to_hints(signals: list[str]) -> list[str]:
    hint_map = {
        "code_artifact": "code_artifact_hint",
        "direct_answer_question": "direct_answer_hint",
        "workspace_read": "workspace_read_hint",
        "multi_stage_scope": "medium_scope_hint",
        "tooling_or_verification": "tooling_hint",
        "small_edit_signal": "small_edit_hint",
        "debug_signal": "debug_hint",
        "review_or_risk_check": "review_hint",
    }
    hints: list[str] = []
    seen: set[str] = set()
    for signal in signals:
        hint = hint_map.get(str(signal))
        if hint and hint not in seen:
            hints.append(hint)
            seen.add(hint)
    return hints
