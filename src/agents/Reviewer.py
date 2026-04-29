"""
Reviewer Agent - 代码审查员节点
负责分析沙盒测试中的报错信息，提供诊断报告
新增 diff 分析与错误分类能力
"""

import difflib
import logging
import os
import re

from langchain_core.messages import AIMessage, SystemMessage

from src.core.config import WORKSPACE_DIR
from src.core.context_manager import (
    build_reviewer_context,
    estimate_messages_tokens,
)
from src.core.llm_engine import llm
from src.core.metrics import metrics
from src.core.repo_map import generate_repo_map
from src.core.state import AgentState, check_cancelled

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join(WORKSPACE_DIR, ".backups")


# ---------------------------------------------------------------
# Syntax-error quick check (regex fallback, before LLM call)
# ---------------------------------------------------------------

_SYNTAX_ERR_RE = re.compile(
    r"("
    r"SyntaxError"
    r"|IndentationError"
    r"|TabError"
    r"|IndentationError:\s"
    r"|TabError:"
    r")",
    re.IGNORECASE,
)

_IMPORT_ERR_RE = re.compile(
    r"("
    r"ModuleNotFoundError"
    r"|ImportError"
    r"|No module named"
    r")",
    re.IGNORECASE,
)


def classify_syntax_error(error_trace: str) -> str | None:
    """Quick regex-based preclassification of the error trace.

    Returns a hint string like "LIKELY_SYNTAX_ERROR: ..." or
    "LIKELY_IMPORT_ERROR: ..." so the LLM can prioritise accordingly.
    """
    if _SYNTAX_ERR_RE.search(error_trace):
        return "LIKELY_SYNTAX_ERROR: The trace contains Python parse/indent/tab errors."
    if _IMPORT_ERR_RE.search(error_trace):
        return "LIKELY_IMPORT_ERROR: The trace indicates a missing module or bad import."
    return None


# ---------------------------------------------------------------
# Diff builder: compare active_files against their backups
# ---------------------------------------------------------------

def get_changed_files_diff(state: AgentState) -> str:
    """Build a unified diff of every active file against its most recent backup.

    Returns a consolidated string with one diff section per file.
    """
    active_files = state.get("active_files", [])
    if not active_files:
        return "(No active files to diff.)"

    sections: list[str] = []

    for rel_path in active_files:
        # Reconstruct the workspace absolute path (mirrors _get_safe_filepath)
        workspace_abs = os.path.abspath(WORKSPACE_DIR)
        current_path = os.path.abspath(os.path.join(workspace_abs, rel_path))

        if not os.path.exists(current_path):
            sections.append(f"--- {rel_path} (file deleted or never existed) ---\n")
            continue

        # Find the most recent backup for this file
        safe_name = rel_path.replace(os.sep, "_")
        pattern = f"{safe_name}.bak."

        try:
            backups = sorted(
                f for f in os.listdir(BACKUP_DIR) if f.startswith(pattern)
            )
        except FileNotFoundError:
            sections.append(f"--- {rel_path} (no backup directory) ---\n")
            continue

        if not backups:
            sections.append(f"--- {rel_path} (no backup found, this is a new file) ---\n")
            continue

        latest_backup = os.path.join(BACKUP_DIR, backups[-1])
        try:
            with open(latest_backup, encoding="utf-8") as f:
                backup_lines = f.read().splitlines(keepends=True)
        except Exception:
            backup_lines = []

        try:
            with open(current_path, encoding="utf-8") as f:
                current_lines = f.read().splitlines(keepends=True)
        except Exception:
            current_lines = []

        diffs = list(
            difflib.unified_diff(
                backup_lines,
                current_lines,
                fromfile=f"{rel_path} (backup)",
                tofile=f"{rel_path} (current)",
            )
        )

        if diffs:
            sections.append(
                f"--- Diff for {rel_path} (backup -> current) ---\n"
                + "".join(diffs)
            )
        else:
            sections.append(
                f"--- Diff for {rel_path}: no changes detected ---\n"
            )

    if not sections:
        return "(No diffs available.)"

    return "\n".join(sections)


# ---------------------------------------------------------------
# Reviewer node
# ---------------------------------------------------------------

async def reviewer_node(state: AgentState):
    """
    Reviewer 节点 (Critic)（异步）：不写代码，只看报错和 diff，
    负责提供"诊断报告"并分类错误类型。

    Returns:
        dict: 更新的状态，包含诊断报告消息
    """
    check_cancelled(state)
    logger.info("正在分析报错原因，生成诊断报告...")

    # -- Build diff & syntax hints before LLM call --
    diff_report = get_changed_files_diff(state)
    error_trace = state.get("error_trace", "")
    syntax_hint = classify_syntax_error(error_trace)

    # 🌟 使用上下文管理器构建优化上下文 (传递 LLM 实例支持智能摘要)
    workspace_map = generate_repo_map()
    filtered_messages = build_reviewer_context(state, workspace_map, llm=llm)

    # 🌟 在系统提示中注入 diff 信息和错误分类引导
    # 遍历消息找到 SystemMessage 并扩展它
    for i, msg in enumerate(filtered_messages):
        if isinstance(msg, SystemMessage):
            original_content = msg.content
            diff_section = "" if diff_report.startswith("(No") else (
                "\n\n【文件变更 Diff】（对比修改前后的版本）\n"
                + diff_report
                + "\n"
            )
            syntax_hint_section = (
                f"\n【预分类提示】{syntax_hint}\n" if syntax_hint else ""
            )
            # 错误分类指引
            classification_guide = """
【错误分类指引】请在诊断报告的开头用一行标明错误类型：
- SYNTAX_ERROR: Python 语法/缩进/Tab/导入错误（无法通过编译的错误）
- RUNTIME_ERROR: 运行时错误（缺少模块、文件不存在、变量未定义、类型错误等）
- LOGICAL_ERROR: 逻辑错误（算法错误、条件判断错误、边界条件错误、off-by-one 等）

使用格式: [ERROR_TYPE: SYNTAX_ERROR|RUNTIME_ERROR|LOGICAL_ERROR]

结合上述 Diff 信息，你的诊断应该更加精准。
注意观察 diff 中新增或删除的行，判断改动是否引入了新的问题。
""" + diff_section + syntax_hint_section
            filtered_messages[i] = SystemMessage(
                content=original_content + classification_guide
            )
            break

    # 🌟 Token 监控 (包含 diff 后的上下文)
    token_count = estimate_messages_tokens(filtered_messages)
    logger.debug(f"上下文 Token 估算: ~{token_count} tokens (含 diff)")

    # Record LLM call metrics
    start = metrics.record_llm_call_start()

    try:
        response = await llm.ainvoke(filtered_messages)
    except Exception as e:
        logger.error(f"Reviewer LLM 调用失败: {e}")
        raise RuntimeError(f"Reviewer 节点 LLM 调用失败（已重试），图执行终止: {e}") from e

    metrics.record_llm_call_end(
        start,
        tokens_used=token_count,
        node_name="Reviewer",
    )

    # 包装成友好的提示加入对话流
    review_msg = AIMessage(
        content=f"🩺 **Reviewer 诊断报告:**\n{response.content}",
        name="Reviewer",
    )

    return {
        "messages": [review_msg],
        "coder_step_count": 0,  # Reset so Coder gets a fresh budget in the repair cycle
    }
