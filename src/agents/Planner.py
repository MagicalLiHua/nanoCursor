import logging
from typing import List
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, AIMessage

from src.core.repo_map import generate_repo_map
from src.core.state import AgentState
from src.core.llm_engine import llm
from src.core.context_manager import (
    build_planner_context,
    estimate_messages_tokens,
    update_memory_summary,
)
from src.core.metrics import metrics
from src.tools.file_tools import read_file, list_directory

logger = logging.getLogger(__name__)


class PlanOutput(BaseModel):
    plan: str = Field(description="详细的分步执行计划，必须包含具体的测试用例和边界情况检查")
    files: List[str] = Field(description="需要修改或查看的本地目标文件路径列表", default_factory=list)

# 实例化 Pydantic 解析器
parser = PydanticOutputParser(pydantic_object=PlanOutput)


async def planner_node(state: AgentState):
    # 动态获取最新的仓库地图
    current_repo_map = generate_repo_map()
    planner_llm = llm.bind_tools([read_file, list_directory])
    logger.info("正在进行架构思考与探索...")
    format_instructions = parser.get_format_instructions()

    # 🌟 使用上下文管理器构建优化上下文 (传递 LLM 实例支持智能摘要)
    filtered_messages = build_planner_context(state, current_repo_map, llm=llm)

    # 🌟 Token 监控 (使用 tiktoken 精确计数)
    token_count = estimate_messages_tokens(filtered_messages)
    logger.debug(f"上下文 Token 估算: ~{token_count} tokens")

    # 正常调用 LLM (它已经绑定了 tools)
    start = metrics.record_llm_call_start()
    response = await planner_llm.ainvoke(filtered_messages)
    metrics.record_llm_call_end(start, tokens_used=token_count, node_name="Planner")

    # 如果大模型调用了工具，就直接返回消息，进入图的 Tool 循环
    if getattr(response, 'tool_calls', []):
        logger.info("决定调用工具探索项目...")
        return {"messages": [response]}

    # 使用 Pydantic 解析器替代脆弱的正则
    logger.info("探索完毕，生成最终计划！")

    try:
        # parser.invoke 会自动处理各种边界情况（剥离 markdown、处理转义符）
        parsed_result = parser.invoke(response.content)
        new_plan = parsed_result.plan
        target_files = parsed_result.files
    except Exception as e:
        logger.warning(f"结构化解析失败，触发兜底机制。错误: {e}")
        new_plan = response.content
        target_files = []

    display_content = f"**架构师最终计划:**\n{new_plan}\n\n **目标文件:** {', '.join(target_files) if target_files else '无'}"
    plan_message = AIMessage(content=display_content, name="Planner")

    # 🌟 更新记忆摘要
    current_summary = state.get("memory_summary")
    all_messages = state.get("messages", [])
    updated_summary = update_memory_summary(current_summary, all_messages)

    return {
        "messages": [plan_message],
        "current_plan": new_plan,
        "active_files": target_files,
        "retry_count": 0,
        "error_trace": "",
        "coder_step_count": 0,  # 重置 Coder 步数计数器
        "memory_summary": updated_summary,
    }
