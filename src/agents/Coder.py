from langchain_core.messages import SystemMessage
from src.core.state import AgentState
from src.core.llm_engine import llm


# def coder_node(state: AgentState):
#     """
#     Coder 节点：读取 Planner 的计划，结合报错信息（如果有），编写并写入 Python 代码。
#     """
#     print("💻 [Coder] 正在奋笔疾书中...")
#
#     # 1. 从 State 中提取工程上下文
#     plan = state.get("current_plan", "暂无计划")
#     active_files = state.get("active_files", [])
#     error_trace = state.get("error_trace", "")
#
#     # 2. 构造 Coder 的 System Prompt
#     system_prompt = f"""你是一位精通软件工程的专家。
# 你的目标是编写或修改代码，以满足 Planner（规划者）的要求，或修复 Reviewer（审查者）发现的 Bug。
# 你现在的工作目录被限制在一个安全的本地沙盒中。
#
# 【当前执行计划】
# {plan}
#
# 【目标文件】
# {', '.join(active_files) if active_files else '未指定'}
#
# 关于文件操作的关键指令：
# 1. 如果你需要创建一个**全新**的文件，请使用 `write_file` 工具。
# 2. 如果你是要**修改**一个已存在的文件，你**必须使用** `edit_file` 工具。
#    - 严禁使用 `write_file` 重新编写整个文件。
#    - 在使用 `edit_file` 时，`search_block` 必须与原文件中一段连续的代码行**完全匹配**，包括空格和缩进。
#    - 尽量保持 `search_block` 简短，但要足够长以确保其在文件中的唯一性。
#
# 在调用工具之前，请先进行逻辑思考，按步骤分析任务。
# """
#
#     # 3. 动态 Prompt 注入：如果存在上一轮的报错信息，立刻进入 "Debug 模式"
#     if error_trace:
#         print("🔧 [Coder] 检测到报错信息，进入 Debug 修复模式...")
#         system_prompt += f"\n\n🚨 【上一轮运行报错信息，请务必修复该 Bug】\n{error_trace}"
#
#     # 4. 组装消息
#     messages = [SystemMessage(content=system_prompt)] + state["messages"]
#
#     # 6. 调用大模型
#     response = llm.invoke(messages)
#
#     # # 🚨 诊断打印
#     # print("\n--- LLM 返回的纯文本内容 (content) ---")
#     # print(response.content)
#     # print("\n--- LLM 触发的工具调用 (tool_calls) ---")
#     # print(response.tool_calls)
#
#     # 7. 返回更新后的状态
#     # 注意：此时 response 可能包含 tool_calls，LangGraph 会将其追加到 messages 中
#     return {"messages": [response]}


def coder_node(state: AgentState):
    print("💻 [Coder] 正在执行代码编写与修改...")

    plan = state.get("current_plan", "暂无计划")
    active_files = state.get("active_files", [])
    error_trace = state.get("error_trace", "")

    system_prompt = f"""你是一位精通软件工程的专家 (Coder)。
    你的目标是执行 Planner 的计划，或修复 Reviewer 发现的 Bug。
    
    【当前执行计划】
    {plan}
    
    【目标文件】
    {', '.join(active_files) if active_files else '未指定'}
    
    🚨 【文件操作的强制性规范】
    1. 探索：你可以使用 `list_directory` 确认文件路径。
    2. 创建：使用 `write_file` 创建全新文件。
    3. 修改：你**必须先使用** `read_file` 工具读取文件的最新内容，然后再使用 `edit_file` 工具进行精准替换。
       - `search_block` 必须与你刚才读到的内容完全匹配（包括空格和缩进）。
    4. 交付：当你认为所有代码修改都已经完成，不需要再调用任何工具时，请直接回复文本 "DONE" 并简述你的修改，系统会自动进入沙盒测试。
    """

    if error_trace:
        print("🔧 [Coder] 检测到报错信息，进入 Debug 修复模式...")
        system_prompt += f"\n\n🚨 【沙盒运行报错信息，请先使用 read_file 查看文件，再修复 Bug】\n{error_trace}"

    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = llm.invoke(messages)

    if getattr(response, 'tool_calls', []):
        print(f"🛠️ [Coder] 调用工具执行操作: {[t['name'] for t in response.tool_calls]}")

    return {"messages": [response]}