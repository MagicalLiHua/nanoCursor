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


ANALYSIS_ONLY_KEYWORDS = [
    "只分析", "只读", "分析一下", "帮我看看", "检查一下这个项目",
    "有什么问题", "review the code", "code review", "只查看",
    "分析.*不.*改", "不.*修改.*只",
]

SMALL_PATCH_KEYWORDS = [
    "小改", "小修", "一行", "typo", "拼写", "配置", "改个", "修个",
    "简单", "很快", "就改一个", "调整一下",
]

BUG_FIX_KEYWORDS = [
    "bug", "修复", "报错", "错误", "异常", "失败", "崩溃", "fix",
    "debug", "defect", "regression", "回归",
]

REFACTOR_KEYWORDS = [
    "重构", "重写", "整理", "拆分", "合并", "refactor", "restructure",
    "clean up", "清理", "优化结构",
]

DOCS_ONLY_KEYWORDS = [
    "文档", "readme", "说明", "注释", "doc", "docs", "documentation",
    "写清楚", "解释",
]

FEATURE_INTENT_KEYWORDS = [
    "创建", "新建", "实现", "开发", "生成", "做一个", "支持", "命令",
    "测试", "pytest", "unittest", "cli", "api", "接口", "页面", "组件",
    "工具", "脚本", "保存", "读取", "新增", "删除", "完成",
]

CODE_ARTIFACT_PATTERNS = [
    r"\b[\w\-]+\.(py|js|ts|tsx|jsx|html|css|json|yaml|yml|toml|mdx)\b",
    r"\btests?/",
    r"\bpytest\b",
    r"\bunittest\b",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _has_feature_intent(text: str) -> bool:
    return _matches_any(text, FEATURE_INTENT_KEYWORDS) or _matches_any(text, CODE_ARTIFACT_PATTERNS)


def select_strategy(prompt: str) -> str:
    """Select the best execution strategy for a user prompt.

    Uses keyword matching — deterministic and fast.
    Falls back to 'feature_delivery' if no keywords match.
    """
    text = (prompt or "").lower()
    if _matches_any(text, ANALYSIS_ONLY_KEYWORDS):
        return "analysis_only"
    if _matches_any(text, SMALL_PATCH_KEYWORDS):
        return "small_patch"
    if _matches_any(text, BUG_FIX_KEYWORDS):
        return "bug_fix"
    if _matches_any(text, REFACTOR_KEYWORDS):
        return "refactor"
    if _has_feature_intent(text):
        return "feature_delivery"
    if _matches_any(text, DOCS_ONLY_KEYWORDS):
        return "docs_only"
    return "feature_delivery"


def get_strategy_definition(strategy_id: str) -> dict[str, Any]:
    """Return strategy metadata dict."""
    return STRATEGY_DEFS.get(strategy_id, STRATEGY_DEFS["feature_delivery"])


def get_tool_policy(strategy_id: str) -> ToolPolicy:
    """Return the ToolPolicy for a given strategy."""
    return policy_for_strategy(strategy_id)
