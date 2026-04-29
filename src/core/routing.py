"""
路由控制模块 - 封装 LangGraph 图的节点间路由决策逻辑。
将其从 run.py 中抽取出来，使得路由逻辑可以被独立测试。
"""

from src.core.config import MAX_CODER_STEPS, MAX_PLANNER_STEPS
from src.core.state import AgentState


def route_after_planner(state: AgentState):
    """判断 Planner 是在探索工具，还是做好了计划"""
    messages = state.get("messages", [])
    if not messages:
        return "coder"
    last_message = messages[-1]
    has_tool_calls = getattr(last_message, 'tool_calls', [])
    if not has_tool_calls:
        return "coder"

    # 防止 Planner 无限探索
    planner_step_count = sum(1 for msg in state.get("messages", [])
                             if getattr(msg, 'name', None) == "Planner" and getattr(msg, 'tool_calls', []))
    if planner_step_count >= MAX_PLANNER_STEPS:
        return "coder"
    return "planner_tools"


def route_after_coder(state: AgentState):
    """判断 Coder 是在敲代码/看文件，还是全部完工了"""
    from src.core.logger import logger

    coder_step_count = state.get("coder_step_count", 0)
    max_coder_steps = state.get("max_coder_steps", MAX_CODER_STEPS)
    messages = state.get("messages", [])
    if not messages:
        return "sandbox"
    last_message = messages[-1]

    # 检查是否达到最大步数
    if coder_step_count >= max_coder_steps:
        logger.warning(f"[Router] Coder 已达到最大步数限制 ({max_coder_steps})，强制进入沙盒测试")
        return "sandbox"

    if getattr(last_message, 'tool_calls', []):
        return "coder_step_counter"  # 先去计数器节点

    logger.info("[Router] Coder 认为修改已完成。移交沙盒测试...")
    return "sandbox"  # 没调用工具说明代码敲完了，去测试
