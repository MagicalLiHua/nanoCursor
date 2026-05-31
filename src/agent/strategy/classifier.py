"""LLM-based intent classifier for strategy selection.

Uses a lightweight LLM call to classify user prompts into strategies
and complexity levels. Falls back to None on failure so callers can
use keyword-based classification as a fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Per-session cache: prompt_hash -> result
_classify_cache: dict[str, dict[str, Any]] = {}

# Timeout for the LLM call (seconds)
_CLASSIFIER_TIMEOUT = 8

# Minimum confidence to accept LLM classification
_MIN_CONFIDENCE = 0.6

# Feature toggle
_ENABLED = os.getenv("LLM_CLASSIFIER_ENABLED", "true").lower() in ("1", "true", "yes")

_CLASSIFIER_PROMPT = """你是 nanoCursor 的任务分类器。根据用户的编程请求，输出 JSON 分类结果。

可选策略：
- analysis_only: 只分析不修改（"解释一下这个模块"、"分析架构"、"帮我看看"）
- small_patch: 小改动（修 typo、改一行配置、小 UI 调整、拼写错误）
- bug_fix: 修 bug（报错、异常行为、回归、崩溃、失败）
- refactor: 重构（整理代码、优化结构、拆分模块、不改功能）
- docs_only: 只改文档（README、注释、API 文档、说明文档）
- feature_delivery: 新功能（创建新模块、实现新需求、端到端开发、新增接口）

可选复杂度：
- simple: 一轮对话能解决，不需要子 Agent（闲聊、简单问题）
- small_code: 需要改代码但逻辑简单（小 patch、单文件修改）
- medium: 需要规划 + 实现 + 验证（新功能、bug 修复）
- high_risk: 涉及安全、数据库、部署、多模块联动

可选角色（按需选择，总是包含 lead）：
- planner: 需要拆解任务时
- coder: 需要写代码时
- tester: 需要验证时
- reviewer: 需要审查时
- designer: 涉及 UI/UX 时
- devops: 涉及部署/CI/CD 时
- security: 涉及安全/权限时

用户请求：{prompt}

{context_section}

只输出 JSON，格式如下，不要输出其他内容：
{{"strategy": "...", "complexity": "...", "needed_roles": ["lead", ...], "confidence": 0.0-1.0, "rationale": "一句话解释分类理由"}}"""


def _cache_key(prompt: str, conversation_summary: str = "") -> str:
    """Generate a cache key from prompt + conversation summary."""
    raw = f"{prompt.strip()}||{conversation_summary.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def classify_with_llm(
    prompt: str,
    conversation_summary: str = "",
    workspace_summary: dict | None = None,
) -> dict[str, Any] | None:
    """Classify a user prompt using LLM.

    Returns a dict with keys: strategy, complexity, needed_roles, confidence, rationale.
    Returns None if classification fails, is disabled, or confidence is too low.
    """
    if not _ENABLED:
        return None

    if not prompt or not prompt.strip():
        return None

    # Check cache
    key = _cache_key(prompt, conversation_summary)
    if key in _classify_cache:
        return _classify_cache[key]

    try:
        result = await asyncio.wait_for(
            _call_classifier(prompt, conversation_summary),
            timeout=_CLASSIFIER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM classifier timed out after %ds", _CLASSIFIER_TIMEOUT)
        return None
    except Exception as exc:
        logger.warning("LLM classifier failed: %s", exc)
        return None

    if result and result.get("confidence", 0) >= _MIN_CONFIDENCE:
        _classify_cache[key] = result
        return result

    return None


async def _call_classifier(
    prompt: str,
    conversation_summary: str = "",
) -> dict[str, Any] | None:
    """Make the actual LLM call for classification."""
    from src.infra.llm_config import create_client, MODEL

    client = create_client()

    context_section = ""
    if conversation_summary:
        context_section = f"对话上下文（前几轮在聊什么）：{conversation_summary[:500]}"

    user_message = _CLASSIFIER_PROMPT.format(
        prompt=prompt[:800],
        context_section=context_section,
    )

    resp = await client.messages.create(
        model=MODEL,
        max_tokens=300,
        temperature=0,
        messages=[{"role": "user", "content": user_message}],
    )

    text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    return _parse_result(text)


def _parse_result(text: str) -> dict[str, Any] | None:
    """Parse the LLM's JSON response."""
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM classifier returned invalid JSON: %s", text[:200])
        return None

    # Validate required fields
    if not isinstance(data, dict):
        return None

    strategy = data.get("strategy", "")
    valid_strategies = {
        "analysis_only", "small_patch", "bug_fix",
        "refactor", "docs_only", "feature_delivery",
    }
    if strategy not in valid_strategies:
        logger.warning("LLM classifier returned invalid strategy: %s", strategy)
        return None

    complexity = data.get("complexity", "")
    valid_complexity = {"simple", "small_code", "medium", "high_risk"}
    if complexity not in valid_complexity:
        logger.warning("LLM classifier returned invalid complexity: %s", complexity)
        return None

    needed_roles = data.get("needed_roles", ["lead"])
    if not isinstance(needed_roles, list):
        needed_roles = ["lead"]
    if "lead" not in needed_roles:
        needed_roles.insert(0, "lead")

    confidence = data.get("confidence", 0)
    if not isinstance(confidence, (int, float)):
        confidence = 0
    confidence = max(0.0, min(1.0, float(confidence)))

    return {
        "strategy": strategy,
        "complexity": complexity,
        "needed_roles": needed_roles,
        "confidence": confidence,
        "rationale": str(data.get("rationale", "")),
    }


def clear_cache() -> None:
    """Clear the classifier cache (useful for testing)."""
    _classify_cache.clear()
