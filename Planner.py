import json
from langchain_core.messages import SystemMessage, AIMessage
from state import AgentState
from llm_engine import llm


# ==========================================
# 3. 核心节点定义 (Nodes)
# ==========================================

def planner_node(state: AgentState):
    """
    Planner 节点：负责理解需求、制定计划，并圈定需要修改的文件作用域。
    """
    print("🧠 [Planner] 正在分析需求并制定计划...")

    # 1. 构造 System Prompt，强调架构师的角色，并严格约束输出格式
    system_prompt = """你是一个资深的软件架构师 (Planner)。
    你的唯一任务是理解用户的需求，制定分步的代码开发/修改计划，并圈定需要涉及的本地文件。

    🚨 【关键要求：测试驱动开发 (TDD)】
    为了验证代码的逻辑正确性，你的计划中必须包含具体的测试用例！
    你必须明确要求 Coder 在 Python 文件的末尾使用 `assert` 语句或者 `print` 对比预期结果来验证逻辑。

    你必须严格以 JSON 格式输出，只包含以下两个字段：
    1. "plan": (字符串) 详细的执行计划，必须包含具体的测试用例和边界情况检查。
    2. "files": (字符串列表) 本次任务需要修改或创建的具体文件路径列表。

    示例输出：
    {
        "plan": "1. 在 math_utils.py 中编写 add(a, b) 函数。\n2. 在文件末尾添加测试断言：assert add(2, 3) == 5 和 assert add(-1, 1) == 0。",
        "files": ["math_utils.py"]
    }"""

    # 2. 组装对话历史
    messages = [SystemMessage(content=system_prompt)] + state["messages"]

    # 3. 调用大模型 (MVP 技巧：使用 format="json" 强制 Ollama 输出原生 JSON，避免 Markdown 格式解析报错)
    response = llm.bind(format="json").invoke(messages)

    # 4. 解析输出并提取工程上下文
    try:
        # 反序列化大模型生成的 JSON
        result = json.loads(response.content)
        new_plan = result.get("plan", "计划生成异常")
        target_files = result.get("files", [])
    except json.JSONDecodeError:
        # 兜底机制：如果大模型幻觉没有输出标准 JSON
        print(f"⚠️ [Planner] JSON 解析失败，原文: {response.content}")
        new_plan = "⚠️ 无法解析计划结构，请人类检查。"
        target_files = []

    # 5. 组装一条易于阅读的 AI 消息，供对话历史查看
    display_content = f"📝 **架构师计划:**\n{new_plan}\n\n📁 **目标文件:** {', '.join(target_files) if target_files else '无'}"
    plan_message = AIMessage(content=display_content, name="Planner")

    # 6. 返回需要更新到 AgentState 的字段（这里体现了 State 的魅力）
    return {
        "messages": [plan_message],  # add_messages 会将其追加到历史记录中
        "current_plan": new_plan,  # 覆盖工程上下文：当前计划
        "active_files": target_files,  # 覆盖工程上下文：当前关注的文件
        "retry_count": 0,  # 每次产生新计划时，重置沙盒报错重试次数
        "error_trace": ""  # 清空上一轮的旧报错信息
    }