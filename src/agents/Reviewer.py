from langchain_core.messages import SystemMessage, AIMessage

from src.core.repo_map import generate_repo_map
from src.core.state import AgentState
from src.core.llm_engine import llm
from src.core.context_manager import (
    build_reviewer_context,
    estimate_messages_tokens,
)


def reviewer_node(state: AgentState):
    """
    Reviewer 节点 (Critic)：不写代码，只看报错，负责提供"诊断报告"。
    """
    print("[Reviewer] 正在分析报错原因，生成诊断报告...")

    # 🌟 使用上下文管理器构建优化上下文 (传递 LLM 实例支持智能摘要)
    workspace_map = generate_repo_map()
    filtered_messages = build_reviewer_context(state, workspace_map, llm=llm)

    # 🌟 Token 监控 (使用 tiktoken 精确计数)
    token_count = estimate_messages_tokens(filtered_messages)
    print(f"[Reviewer] 上下文 Token 估算: ~{token_count} tokens")

    # Reviewer 不需要调用工具，直接输出诊断意见
    response = llm.invoke(filtered_messages)

    # 包装成友好的提示加入对话流
    review_msg = AIMessage(content=f"🩺 **Reviewer 诊断报告:**\n{response.content}", name="Reviewer")

    return {"messages": [review_msg]}