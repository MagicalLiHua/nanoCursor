from langchain_core.messages import SystemMessage, ToolMessage

from src.core.repo_map import generate_repo_map
from src.core.state import AgentState
from src.core.llm_engine import llm
from src.core.config import WINDOW_SIZE
from src.tools.file_tools import tools


def coder_node(state: AgentState):
    print("[Coder] 正在执行代码编写与修改...")
    coder_llm = llm.bind_tools(tools)

    plan = state.get("current_plan", "暂无计划")
    active_files = state.get("active_files", [])
    error_trace = state.get("error_trace", "")

    workspace_map = generate_repo_map()

    change_log = []
    for msg in state["messages"]:
        # ToolMessage 且是修改文件的工具
        if msg.type == "tool" and msg.name in ["edit_file", "write_file"]:
            change_log.append(f"- {msg.content}")

    change_log_str = "\n".join(change_log) if change_log else "暂无文件修改记录。"

    # 1. 构造 System Prompt (这部分保持不变)
    system_prompt = f"""你是一位精通软件工程的专家 (Coder)。
    你的目标是执行 Planner 的计划，或修复 Reviewer 发现的 Bug。
    
    【当前工作区概览 (Repo Map)】
    作为你编写代码的上下文参考（函数签名/类名）：
    {workspace_map}

    【当前执行计划】
    {plan}
    
    【本轮对话中，你已经完成的改动记录 (Change Log)】
    {change_log_str}

    【目标文件】
    {', '.join(active_files) if active_files else '未指定'}

    【Coder 的职责边界】
    1. 你只是一个“代码手术刀”。你没有执行、运行、测试或验证代码的环境与工具。
    2. 如果计划中要求“测试”，你的任务仅仅是把测试逻辑**写到测试文件里**。
    3. 不要在回答中设想代码运行的结果。
    4. 交付：当你认为代码（包括功能和测试文件）已经编写完成，请直接回复文本"DONE"。系统会自动将你写好的文件挂载到 Docker Sandbox 中去运行验证。

    【文件操作的强制性规范】
    1. 创建：使用 `write_file` 创建全新文件。
    2. 修改：你**必须先使用** `read_file` 工具读取文件的最新内容，然后再使用 `edit_file` 工具进行精准替换。
    """

    if error_trace:
        print("[Coder] 检测到报错信息，进入 Debug 修复模式...")
        system_prompt += f"\n\n【沙盒运行报错信息，请先使用 read_file 查看文件，再修复 Bug】\n{error_trace}"
    #
    # # ==========================================
    # # 🌟 核心改造：引入 Context Manager (上下文裁剪)
    # # ==========================================
    # all_messages = state["messages"]
    #
    # # 1. 提取首条用户消息 (永远记住最初的任务)
    # user_msgs = [m for m in all_messages if m.type == "human" or m.type == "user"]
    # first_user_msg = user_msgs[0] if user_msgs else None
    #
    # # 2. 滑动窗口：只保留最近的 k 条消息
    # # 避免前面的长文件读取记录污染上下文
    # # WINDOW_SIZE = 6
    # recent_messages = all_messages[-WINDOW_SIZE:] if len(all_messages) > WINDOW_SIZE else all_messages
    #
    # # 3. 重新组装干净的上下文
    # filtered_messages = [SystemMessage(content=system_prompt)]
    #
    # # 如果最近的消息里不包含最初的任务，强制把它加在前面
    # if first_user_msg and first_user_msg not in recent_messages:
    #     filtered_messages.append(first_user_msg)
    #
    # filtered_messages.extend(recent_messages)

    # ==========================================
    # 改造：安全的上下文管理 (丢弃危险的滑动窗口)
    # ==========================================
    filtered_messages = [SystemMessage(content=system_prompt)]

    for msg in state["messages"]:
        # 拦截 read_file 产生的巨型文本，防止 Token 撑爆，同时保证消息配对不被破坏
        if msg.type == "tool" and getattr(msg, "name", "") == "read_file":
            content = msg.content
            # 如果读取的文件过长，折叠中间部分，只保留头尾
            if len(content) > 3000:
                compressed_content = content[:1500] + "\n\n...[代码过长，中间部分已安全折叠]...\n\n" + content[-1500:]
                # 重新构造 ToolMessage，保留原有的 tool_call_id
                msg = ToolMessage(
                    content=compressed_content,
                    name=msg.name,
                    tool_call_id=msg.tool_call_id
                )
        filtered_messages.append(msg)
    # ==========================================

    # 传入过滤后的精简上下文，让 LLM 专注于当前的计划和修改，不被过多的历史消息干扰
    response = coder_llm.invoke(filtered_messages)

    if getattr(response, 'tool_calls', []):
        print(f"🛠️ [Coder] 调用工具执行操作: {[t['name'] for t in response.tool_calls]}")

    return {"messages": [response]}