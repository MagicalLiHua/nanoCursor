from typing import List
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, AIMessage
from src.core.state import AgentState
from src.core.llm_engine import llm


class PlanOutput(BaseModel):
    plan: str = Field(description="详细的分步执行计划，必须包含具体的测试用例和边界情况检查")
    files: List[str] = Field(description="需要修改或查看的本地目标文件路径列表", default_factory=list)

# 实例化 Pydantic 解析器
parser = PydanticOutputParser(pydantic_object=PlanOutput)


def planner_node(state: AgentState):
    print("[Planner] 正在进行架构思考与探索...")

    system_prompt = """你是一个资深的软件架构师 (Planner)。
    你的任务是理解用户的需求，制定分步的代码开发/修改计划，并圈定需要涉及的本地文件。
    
    【关键能力：探索工作区】
    你不必仅凭猜想！你可以随时调用 `list_directory` 查看项目目录结构，或使用 `read_file` 查看某个文件的具体内容。
    请充分探索工作区，直到你完全明确需要修改哪些文件。
    
    【输出规范：交付计划】
    当你完成了探索，确定了最终的执行计划后，请停止调用工具，并在你的回复中包含一个严格的 Markdown JSON 块，格式如下：
    ```json
    {
        "plan": "1. 详细的执行计划，必须包含具体的测试用例和边界情况检查...",
        "files": ["src/main.py", "tests/test_main.py"]
    }
    """

    messages = [SystemMessage(content=system_prompt)] + state["messages"]

    # 正常调用 LLM (它已经绑定了 tools)
    response = llm.invoke(messages)

    # 1. 如果大模型调用了工具（比如去偷看目录了），就直接返回消息，进入图的 Tool 循环
    if getattr(response, 'tool_calls', []):
        print("[Planner] 决定调用工具探索项目...")
        return {"messages": [response]}

    # ==========================================
    # 使用 Pydantic 解析器替代脆弱的正则
    # ==========================================
    print("[Planner] 探索完毕，生成最终计划！")

    try:
        # parser.invoke 会自动处理各种边界情况（剥离 markdown、处理转义符）
        parsed_result = parser.invoke(response.content)
        new_plan = parsed_result.plan
        target_files = parsed_result.files
    except Exception as e:
        print(f"[Planner] 结构化解析失败，触发兜底机制。错误: {e}")
        new_plan = response.content
        target_files = []

    display_content = f"**架构师最终计划:**\n{new_plan}\n\n **目标文件:** {', '.join(target_files) if target_files else '无'}"
    plan_message = AIMessage(content=display_content, name="Planner")

    return {
        "messages": [plan_message],
        "current_plan": new_plan,
        "active_files": target_files,
        "retry_count": 0,
        "error_trace": ""
    }