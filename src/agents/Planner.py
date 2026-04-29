import logging
import re

from langchain_core.messages import AIMessage, HumanMessage

from src.core.context_manager import (
    build_planner_context,
    estimate_messages_tokens,
    update_memory_summary,
)
from src.core.llm_engine import llm
from src.core.metrics import metrics
from src.core.repo_map import generate_repo_map
from src.core.state import AgentState, check_cancelled
from src.tools.file_tools import list_directory, read_file

logger = logging.getLogger(__name__)


def planner_node(state: AgentState):
    check_cancelled(state)
    # 动态获取最新的仓库地图
    current_repo_map = generate_repo_map()
    planner_llm = llm.bind_tools([read_file, list_directory])
    logger.info("正在进行架构思考与探索...")

    # 🌟 使用上下文管理器构建优化上下文 (传递 LLM 实例支持智能摘要)
    filtered_messages = build_planner_context(state, current_repo_map, llm=llm)

    # 🌟 Token 监控 (使用 tiktoken 精确计数)
    token_count = estimate_messages_tokens(filtered_messages)
    logger.debug(f"上下文 Token 估算: ~{token_count} tokens")

    # 追加自然语言表达指令
    instruction_msg = HumanMessage(
        content=(
            "请根据上述需求制定分步的开发计划，以清晰的自然语言回复。"
            "最后用【目标文件】标记列出涉及或需要查看的所有本地文件路径。"
        )
    )
    filtered_messages.append(instruction_msg)

    # 正常调用 LLM (它已经绑定了 tools)
    start = metrics.record_llm_call_start()
    response = planner_llm.invoke(filtered_messages)
    metrics.record_llm_call_end(start, tokens_used=token_count, node_name="Planner")

    # 如果大模型调用了工具，就直接返回消息，进入图的 Tool 循环
    if getattr(response, 'tool_calls', []):
        logger.info("决定调用工具探索项目...")
        return {"messages": [response]}

    # 从自然语言回复中提取计划和目标文件
    logger.info("探索完毕，生成最终计划！")

    raw_text = response.content.strip()
    # 清理 markdown 代码块
    fence = re.match(r'^```(?:md|markdown)?\s*\n?(.*?)```$', raw_text, re.DOTALL | re.IGNORECASE)
    if fence:
        raw_text = fence.group(1).strip()

    # 提取目标文件
    target_files = []
    files_section_match = re.search(r'【目标文件】\s*(.*?)(?:$|【)', raw_text, re.DOTALL)
    if files_section_match:
        files_text = files_section_match.group(1).strip()
        target_files = [f.strip().strip('`,-*') for f in re.split(r'[\n,，\s]+', files_text) if f.strip()]
        # 移除 "【目标文件】" 本身混入的情况
        target_files = [f for f in target_files if not f.startswith('【')]

    plan_message = AIMessage(content=raw_text, name="Planner")

    # 🌟 更新记忆摘要
    current_summary = state.get("memory_summary")
    all_messages = state.get("messages", [])
    updated_summary = update_memory_summary(current_summary, all_messages)

    return {
        "messages": [plan_message],
        "current_plan": raw_text,
        "active_files": target_files,
        "retry_count": 0,
        "error_trace": "",
        "coder_step_count": 0,  # 重置 Coder 步数计数器
        "memory_summary": updated_summary,
    }
