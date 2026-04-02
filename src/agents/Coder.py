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
from src.tools.file_tools import tools

logger = logging.getLogger(__name__)


def coder_node(state: AgentState):
    """
    Coder 节点执行函数。
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
    response = coder_llm.invoke(filtered_messages)

    if getattr(response, 'tool_calls', []):
        tool_names = [t['name'] for t in response.tool_calls]
        logger.info(f"调用工具执行操作: {tool_names}")

    return {
        "messages": [response],
        "memory_summary": updated_summary,
    }