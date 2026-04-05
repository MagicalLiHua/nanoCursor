"""
Coder Agent - 工程师节点
负责执行代码编写与修改任务
"""

import logging
from langchain_core.messages import SystemMessage, ToolMessage

from src.core.repo_map import generate_repo_map
from src.core.state import AgentState
from src.core.llm_engine import llm
from src.core.context_manager import (
    build_coder_context,
    estimate_messages_tokens,
    update_memory_summary,
)
from src.core.metrics import metrics
from src.tools.file_tools import tools

logger = logging.getLogger(__name__)


async def coder_node(state: AgentState):
    """
    Coder 节点执行函数（异步）。
    根据当前计划和报错信息，执行代码编写或修改任务。

    Returns:
        dict: 更新的状态，包含 LLM 响应和记忆摘要
    """
    logger.info("正在执行代码编写与修改...")
    coder_llm = llm.bind_tools(tools)

    # 🌟 使用上下文管理器构建优化上下文 (传递 LLM 实例支持智能摘要)
    workspace_map = generate_repo_map()
    filtered_messages = build_coder_context(state, workspace_map, llm=llm)

    # 🌟 Token 监控 (使用 tiktoken 精确计数)
    token_count = estimate_messages_tokens(filtered_messages)
    logger.debug(f"上下文 Token 估算: ~{token_count} tokens")

    # 🌟 更新记忆摘要
    current_summary = state.get("memory_summary")
    new_messages = state.get("messages", [])
    updated_summary = update_memory_summary(current_summary, new_messages)

    # 传入优化后的上下文
    start = metrics.record_llm_call_start()

    try:
        response = await coder_llm.ainvoke(filtered_messages)
    except Exception as e:
        logger.error(f"Coder LLM 调用失败: {e}")
        metrics.record_llm_call_end(start, tokens_used=token_count, node_name="Coder")
        raise RuntimeError(f"Coder 节点 LLM 调用失败（已重试），图执行终止: {e}") from e

    metrics.record_llm_call_end(start, tokens_used=token_count, node_name="Coder")

    if getattr(response, 'tool_calls', []):
        tool_names = [t['name'] for t in response.tool_calls]
        logger.info(f"调用工具执行操作: {tool_names}")

    # 记录本次 Coder 调用的具体工具意图到 modification_log
    modification_log = state.get("modification_log", [])
    if getattr(response, 'tool_calls', []):
        for tc in response.tool_calls:
            tool_name = tc['name']
            tool_args = tc.get('args', {})
            target = tool_args.get('filename', '未知')
            entry = f"{tool_name} -> {target}"
            modification_log.append(entry)
    else:
        content_preview = response.content[:100] if response.content else "(空)"
        modification_log.append(f"Coder 输出: {content_preview}")

    return {
        "messages": [response],
        "memory_summary": updated_summary,
        "modification_log": modification_log,
    }