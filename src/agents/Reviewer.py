from langchain_core.messages import SystemMessage, AIMessage
from src.core.state import AgentState
from src.core.llm_engine import llm


def reviewer_node(state: AgentState):
    """
    Reviewer 节点 (Critic)：不写代码，只看报错，负责提供“诊断报告”。
    """
    print("🔍 [Reviewer] 正在分析报错原因，生成诊断报告...")

    error_trace = state.get("error_trace", "")
    plan = state.get("current_plan", "")

    system_prompt = f"""你是一个资深的代码审查员 (Reviewer)。
刚才 Coder 按照计划编写了代码，但在沙盒运行中报错了。

【原始执行计划】
{plan}

【沙盒报错信息】
{error_trace}

你的任务是：
1. 分析报错的根本原因。
2. 给 Coder 提供明确、具体的修改建议（比如指出哪一行逻辑错了，应该怎么改）。
注意：你只用输出自然语言的分析和建议，绝对不要输出完整的代码，代码由 Coder 来写。
"""

    messages = [SystemMessage(content=system_prompt)] + state["messages"]

    # Reviewer 不需要调用工具，直接输出诊断意见
    response = llm.invoke(messages)

    # 包装成友好的提示加入对话流
    review_msg = AIMessage(content=f"🩺 **Reviewer 诊断报告:**\n{response.content}", name="Reviewer")

    return {"messages": [review_msg]}