"""Shared helpers for classifying tool call results."""

from __future__ import annotations

from typing import Any


TOOL_ERROR_PREFIXES = (
    "Error:",
    "错误：",
    "修改失败：",
    "安全拦截：",
    "回滚失败:",
)


def is_tool_error_output(output: Any) -> bool:
    """Return True when a tool-style output represents a failed operation."""
    text = str(output or "").lstrip()
    return text.startswith(TOOL_ERROR_PREFIXES)


def tool_error_message(output: Any) -> str:
    """Strip a known tool error prefix while preserving the original text as fallback."""
    text = str(output or "").lstrip()
    for prefix in TOOL_ERROR_PREFIXES:
        if text.startswith(prefix):
            return text.removeprefix(prefix).strip() or text
    return text
