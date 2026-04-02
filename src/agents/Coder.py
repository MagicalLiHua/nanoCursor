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


def coder_node(state: AgentState):
    print("[Coder] 正在执行代码编写与修改...")
    coder_llm = llm.bind_tools(tools)

    # 🌟 使用上下文管理器构建优化上下文 (传递 LLM 实例支持智能摘要)
    workspace_map = generate_repo_map()
    filtered_messages = build_coder_context(state, workspace_map, llm=llm)

    # 🌟 Token 监控 (使用 tiktoken 精确计数)
    token_count = estimate_messages_tokens(filtered_messages)
    print(f"[Coder] 上下文 Token 估算: ~{token_count} tokens")

    # 🌟 更新记忆摘要
    current_summary = state.get("memory_summary")
    new_messages = state.get("messages", [])
    updated_summary = update_memory_summary(current_summary, new_messages)

    # 传入优化后的上下文
    response = coder_llm.invoke(filtered_messages)

    if getattr(response, 'tool_calls', []):
        print(f"🛠️ [Coder] 调用工具执行操作: {[t['name'] for t in response.tool_calls]}")

    return {
        "messages": [response],
        "memory_summary": updated_summary,
    }
