from langchain_core.messages import SystemMessage
from state import AgentState
from llm_engine import llm

# ==========================================
# 5. 工程师节点定义 (Coder Node)
# ==========================================

def coder_node(state: AgentState):
    """
    Coder 节点：读取 Planner 的计划，结合报错信息（如果有），编写并写入 Python 代码。
    """
    print("💻 [Coder] 正在奋笔疾书中...")

    # 1. 从 State 中提取工程上下文
    plan = state.get("current_plan", "暂无计划")
    active_files = state.get("active_files", [])
    error_trace = state.get("error_trace", "")

    # 2. 构造 Coder 的 System Prompt
    system_prompt = f"""你是一个顶级的 Python 工程师 (Coder)。
你的任务是严格按照架构师的【执行计划】，编写或修改代码。
你现在的工作目录被限制在一个安全的本地沙盒中。

【当前执行计划】
{plan}

【目标文件】
{', '.join(active_files) if active_files else '未指定'}

请编写完整的 Python 代码，并使用 `write_python_file` 工具将代码保存到文件中。
"""

    # 3. 动态 Prompt 注入：如果存在上一轮的报错信息，立刻进入 "Debug 模式"
    if error_trace:
        print("🔧 [Coder] 检测到报错信息，进入 Debug 修复模式...")
        system_prompt += f"\n\n🚨 【上一轮运行报错信息，请务必修复该 Bug】\n{error_trace}"

    # 4. 组装消息
    messages = [SystemMessage(content=system_prompt)] + state["messages"]

    # 6. 调用大模型
    response = llm.invoke(messages)

    # 🚨 诊断打印
    print("\n--- LLM 返回的纯文本内容 (content) ---")
    print(response.content)
    print("\n--- LLM 触发的工具调用 (tool_calls) ---")
    print(response.tool_calls)

    # 7. 返回更新后的状态
    # 注意：此时 response 可能包含 tool_calls，LangGraph 会将其追加到 messages 中
    return {"messages": [response]}