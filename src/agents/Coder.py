from langchain_core.messages import SystemMessage
from src.core.state import AgentState
from src.core.llm_engine import llm
from src.core.config import WINDOW_SIZE


def coder_node(state: AgentState):
    print("[Coder] 正在执行代码编写与修改...")

    plan = state.get("current_plan", "暂无计划")
    active_files = state.get("active_files", [])
    error_trace = state.get("error_trace", "")

    # 1. 构造 System Prompt (这部分保持不变)
    system_prompt = f"""你是一位精通软件工程的专家 (Coder)。
    你的目标是执行 Planner 的计划，或修复 Reviewer 发现的 Bug。

    【当前执行计划】
    {plan}

    【目标文件】
    {', '.join(active_files) if active_files else '未指定'}

    【文件操作的强制性规范】
    1. 探索：你可以使用 `list_directory` 确认文件路径。
    2. 创建：使用 `write_file` 创建全新文件。
    3. 修改：你**必须先使用** `read_file` 工具读取文件的最新内容，然后再使用 `edit_file` 工具进行精准替换。
       - `search_block` 必须与你刚才读到的内容完全匹配（包括空格和缩进）。
    4. 交付：当你认为所有代码修改都已经完成，不需要再调用任何工具时，请直接回复文本 "DONE" 并简述你的修改，系统会自动进入沙盒测试。
    """

    if error_trace:
        print("[Coder] 检测到报错信息，进入 Debug 修复模式...")
        system_prompt += f"\n\n【沙盒运行报错信息，请先使用 read_file 查看文件，再修复 Bug】\n{error_trace}"

    # ==========================================
    # 🌟 核心改造：引入 Context Manager (上下文裁剪)
    # ==========================================
    all_messages = state["messages"]

    # 1. 提取首条用户消息 (永远记住最初的任务)
    user_msgs = [m for m in all_messages if m.type == "human" or m.type == "user"]
    first_user_msg = user_msgs[0] if user_msgs else None

    # 2. 滑动窗口：只保留最近的 k 条消息
    # 避免前面的长文件读取记录污染上下文
    # WINDOW_SIZE = 6
    recent_messages = all_messages[-WINDOW_SIZE:] if len(all_messages) > WINDOW_SIZE else all_messages

    # 3. 重新组装干净的上下文
    filtered_messages = [SystemMessage(content=system_prompt)]

    # 如果最近的消息里不包含最初的任务，强制把它加在前面
    if first_user_msg and first_user_msg not in recent_messages:
        filtered_messages.append(first_user_msg)

    filtered_messages.extend(recent_messages)

    # ==========================================

    # 传入过滤后的精简上下文
    response = llm.invoke(filtered_messages)

    if getattr(response, 'tool_calls', []):
        print(f"🛠️ [Coder] 调用工具执行操作: {[t['name'] for t in response.tool_calls]}")

    return {"messages": [response]}