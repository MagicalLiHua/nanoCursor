"""Strategy planner: select execution strategy based on task type."""

from __future__ import annotations

import re
from typing import Any

from src.agent.strategy.tool_policy import ToolPolicy, policy_for_strategy


# Strategy definitions
STRATEGY_DEFS: dict[str, dict[str, Any]] = {
    "small_patch": {
        "name": "小修小补",
        "description": "小范围修改，少 Agent，快验证。适合拼写、配置、一行修复。",
        "stages": ["intake", "implement", "verify"],
        "required_roles": ["lead", "coder", "tester"],
        "risk_level": "low",
    },
    "feature_delivery": {
        "name": "完整功能交付",
        "description": "完整软件功能，Planner/Coder/Tester/Reviewer 全流程。",
        "stages": ["intake", "plan", "implement", "verify"],
        "required_roles": ["lead", "planner", "coder", "tester"],
        "risk_level": "medium",
    },
    "bug_fix": {
        "name": "Bug 修复",
        "description": "复现、定位、修复、回归测试。",
        "stages": ["intake", "plan", "implement", "verify"],
        "required_roles": ["lead", "planner", "coder", "tester"],
        "risk_level": "medium",
    },
    "refactor": {
        "name": "重构",
        "description": "风险评估、分阶段修改、测试优先。",
        "stages": ["intake", "plan", "implement", "verify", "validate"],
        "required_roles": ["lead", "planner", "coder", "tester", "reviewer"],
        "risk_level": "medium",
    },
    "docs_only": {
        "name": "文档任务",
        "description": "文档任务，不启动写代码阶段。",
        "stages": ["intake", "plan"],
        "required_roles": ["lead", "planner"],
        "risk_level": "low",
    },
    "analysis_only": {
        "name": "只分析",
        "description": "只分析项目，不写入文件。",
        "stages": ["intake", "plan"],
        "required_roles": ["lead", "planner"],
        "risk_level": "low",
    },
}


# Keyword → strategy mapping (checked in order, first match wins)
STRATEGY_RULES: list[tuple[list[str], str]] = [
    (["文档", "readme", "readme", "说明", "注释", "doc", "docs", "documentation",
      "写清楚", "解释"], "docs_only"),
    (["只分析", "只读", "分析一下", "帮我看看", "检查一下这个项目",
      "有什么问题", "review the code", "code review", "只查看",
      "分析.*不.*改", "不.*修改.*只"], "analysis_only"),
    (["小改", "小修", "一行", "typo", "拼写", "配置", "改个", "修个",
      "简单", "很快", "就改一个", "调整一下"], "small_patch"),
    (["bug", "修复", "报错", "错误", "异常", "失败", "崩溃", "fix",
      "debug", "defect", "regression", "回归"], "bug_fix"),
    (["重构", "重写", "整理", "拆分", "合并", "refactor", "restructure",
      "clean up", "清理", "优化结构"], "refactor"),
]


def select_strategy(prompt: str) -> str:
    """Select the best execution strategy for a user prompt.

    Uses keyword matching — deterministic and fast.
    Falls back to 'feature_delivery' if no keywords match.
    """
    text = (prompt or "").lower()
    for keywords, strategy_id in STRATEGY_RULES:
        for kw in keywords:
            if re.search(kw, text, re.IGNORECASE):
                return strategy_id
    return "feature_delivery"


def get_strategy_definition(strategy_id: str) -> dict[str, Any]:
    """Return strategy metadata dict."""
    return STRATEGY_DEFS.get(strategy_id, STRATEGY_DEFS["feature_delivery"])


def get_tool_policy(strategy_id: str) -> ToolPolicy:
    """Return the ToolPolicy for a given strategy."""
    return policy_for_strategy(strategy_id)
