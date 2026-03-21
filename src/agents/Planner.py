from typing import List
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, AIMessage

from src.core.repo_map import generate_repo_map
from src.core.state import AgentState
from src.core.llm_engine import llm
from src.tools.file_tools import read_file


class PlanOutput(BaseModel):
    plan: str = Field(description="详细的分步执行计划，必须包含具体的测试用例和边界情况检查")
    files: List[str] = Field(description="需要修改或查看的本地目标文件路径列表", default_factory=list)

# 实例化 Pydantic 解析器
parser = PydanticOutputParser(pydantic_object=PlanOutput)


def planner_node(state: AgentState):
    # 动态获取最新的仓库地图
    current_repo_map = generate_repo_map()
    planner_llm = llm.bind_tools([read_file])
    print("[Planner] 正在进行架构思考与探索...")
    format_instructions = parser.get_format_instructions()

    system_prompt = f"""你是一个资深的软件架构师 (Planner)。
    你的任务是理解用户的需求，制定分步的代码开发/修改计划，并圈定需要涉及的本地文件。
    
    【当前工作区概览】
    以下是当前项目的文件结构以及关键的函数/类摘要：
    {current_repo_map}
    
    【关键能力：探索工作区】
    如果上述摘要不足以让你做出决定，你可以使用 `read_file` 工具查看某个文件的具体完整内容。
    当然，有时候也出出现用户给你的任务是在一个空白的工作区内开始的，这是被允许的, 如果当前工作目录为空的话你直接按照用户需求指定计划就可以了，不需要探索工作区。
    
    【输出规范：交付计划】
    当你完成了探索，确定了最终的执行计划后，请停止调用工具，并在你的回复中包含一个严格的 Markdown JSON 块，格式如下：
    {format_instructions}
    """

    messages = [SystemMessage(content=system_prompt)] + state["messages"]

    # 正常调用 LLM (它已经绑定了 tools)
    response = planner_llm.invoke(messages)

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