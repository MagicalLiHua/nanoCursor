import uuid
import logging
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from src.core.state import AgentState, checkpointer
from src.agents.Planner import planner_node
from src.agents.Coder import coder_node
from src.agents.Sandbox import sandbox_node
from src.agents.Reviewer import reviewer_node
from src.tools.file_tools import tools, read_file
from src.core.logger import setup_logger

# 初始化日志系统
logger = setup_logger("nanoCursor.run")

# ==========================================
# 1. 初始化图构建器
# ==========================================
workflow = StateGraph(AgentState)

# ==========================================
# 2. 注册所有节点 (Nodes)
# ==========================================

def coder_step_counter(state: AgentState) -> dict:
    """增加 Coder 工具调用步数计数器"""
    current_count = state.get("coder_step_count", 0)
    new_count = current_count + 1
    logger.info(f"[Coder] 第 {new_count} 步工具调用")
    return {"coder_step_count": new_count}

workflow.add_node("planner", planner_node)
workflow.add_node("coder", coder_node)
workflow.add_node("coder_step_counter", coder_step_counter)

# 给 Planner 和 Coder 分别配备独立的工具执行节点
workflow.add_node("planner_tools", ToolNode([read_file]))
workflow.add_node("coder_tools", ToolNode(tools))

workflow.add_node("sandbox", sandbox_node)
workflow.add_node("reviewer", reviewer_node)

# ==========================================
# 3. 定义路由逻辑 (Conditional Edges)
# ==========================================
def route_after_planner(state: AgentState):
    """判断 Planner 是在探索工具，还是做好了计划"""
    last_message = state["messages"][-1]
    if getattr(last_message, 'tool_calls', []):
        return "planner_tools"
    return "coder"  # 没调用工具说明计划做好了，交棒给 Coder


def route_after_coder(state: AgentState):
    """判断 Coder 是在敲代码/看文件，还是全部完工了"""
    coder_step_count = state.get("coder_step_count", 0)
    max_coder_steps = state.get("max_coder_steps", 15)  # 默认最大 15 步
    last_message = state["messages"][-1]
    
    # 检查是否达到最大步数
    if coder_step_count >= max_coder_steps:
        logger.warning(f"[Router] Coder 已达到最大步数限制 ({max_coder_steps})，强制进入沙盒测试")
        return "sandbox"
    
    if getattr(last_message, 'tool_calls', []):
        return "coder_step_counter"  # 先去计数器节点
    
    logger.info("💡 [Router] Coder 认为修改已完成。移交沙盒测试...")
    return "sandbox"  # 没调用工具说明代码敲完了，去测试


def route_after_sandbox(state: AgentState):
    """大脑的中枢：判断测试是否通过，是否需要循环修 Bug"""
    error_trace = state.get("error_trace", "")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    # 1. 成功分支：没有报错信息
    if not error_trace:
        print("[Router] 测试通过！任务完美闭环。")
        return END

    # 2. 失败分支：超出最大重试次数 (兜底机制)
    if retry_count >= max_retries:
        print(f"[Router] 达到最大重试次数 ({max_retries})，大模型尽力了。任务挂起。")
        return END

    # 3. 循环分支：带着报错信息，先交给 Reviewer 分析！
    print(f"[Router] 发现 Bug，交由 Reviewer 分析 (已重试 {retry_count} 次)...")
    return "reviewer"


# ==========================================
# 4. 编排图的连线 (Edges)
# ==========================================


workflow.add_edge(START, "planner")

# --- Planner 微循环 ---
workflow.add_conditional_edges("planner", route_after_planner)
workflow.add_edge("planner_tools", "planner") # 工具执行完，把结果还给 Planner

# --- Coder 微循环 ---
workflow.add_conditional_edges("coder", route_after_coder)
workflow.add_edge("coder_tools", "coder") # 工具执行完，把结果还给 Coder
workflow.add_edge("coder_step_counter", "coder_tools")  # 计数器 -> 工具执行

# --- 测试与反思回环 ---
workflow.add_conditional_edges("sandbox", route_after_sandbox)
workflow.add_edge("reviewer", "coder") # 报错诊断后，打回给 Coder 继续改


# ==========================================
# 5. 编译并打包系统
# ==========================================
app = workflow.compile(
    checkpointer=checkpointer,
)

# ==========================================
# 6. 测试运行入口
# ==========================================
if __name__ == "__main__":
    print("启动 nanoCursor ...")

    # 定义一个唯一的线程 ID (持久化记忆需要)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    user_prompt = """
    我们的项目里有一个文件出了 Bug。我只记得它是一个查找算法相关的 Python 文件，但是我不记得它在哪个目录下了。
    请帮我找出这个文件，读取它，并修复里面导致测试不通过的 Bug。
    """

    initial_state = {
        "messages": [("user", user_prompt)],
        "max_retries": 3,
        "retry_count": 0
    }

    # 开始流式运行我们的图
    try:
        for event in app.stream(initial_state, config=config, stream_mode="values"):
            # 由于我们在每个节点里都写了 print() 来打印状态，这里不用做额外处理
            pass
    except Exception as e:
        print(f"运行过程中发生框架级错误: {e}")

    print("\n运行流转结束！请去你的 workspace 目录下查看战果吧。")