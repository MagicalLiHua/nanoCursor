import uuid
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from src.core.state import AgentState, checkpointer
from src.agents.Planner import planner_node
from src.agents.Coder import coder_node
from src.agents.Sandbox import sandbox_node
from src.agents.Reviewer import reviewer_node
from src.tools.file_tools import tools

# ==========================================
# 1. 初始化图构建器
# ==========================================
workflow = StateGraph(AgentState)

# ==========================================
# 2. 注册所有节点 (Nodes)
# ==========================================
# 把我们写好的函数注册为图中的节点，并起个名字
workflow.add_node("planner", planner_node)
workflow.add_node("coder", coder_node)
# ToolNode 是 LangGraph 自带的，专门用来执行 Coder 发出的工具调用请求
workflow.add_node("tools", ToolNode(tools))
workflow.add_node("sandbox", sandbox_node)
workflow.add_node("reviewer", reviewer_node)

# ==========================================
# 3. 定义路由逻辑 (Conditional Edges)
# ==========================================

def route_after_coder(state: AgentState):
    """判断 Coder 是否调用了工具"""
    messages = state.get("messages", [])
    last_message = messages[-1]

    # 兼容性写法：使用 getattr 防止某些模型没返回 tool_calls 属性报错
    if getattr(last_message, 'tool_calls', []):
        return "tools"

    return END


def route_after_sandbox(state: AgentState):
    """大脑的中枢：判断测试是否通过，是否需要循环修 Bug"""
    error_trace = state.get("error_trace", "")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    # 1. 成功分支：没有报错信息
    if not error_trace:
        print("🎉 [Router] 测试通过！任务完美闭环。")
        return END

    # 2. 失败分支：超出最大重试次数 (兜底机制)
    if retry_count >= max_retries:
        print(f"🛑 [Router] 达到最大重试次数 ({max_retries})，大模型尽力了。任务挂起。")
        return END

    # 3. 循环分支：带着报错信息，先交给 Reviewer 分析！
    print(f"🔄 [Router] 发现 Bug，交由 Reviewer 分析 (已重试 {retry_count} 次)...")
    return "reviewer"


# ==========================================
# 4. 编排图的连线 (Edges)
# ==========================================

# 起点 -> 规划师 -> 工程师
workflow.add_edge(START, "planner")
workflow.add_edge("planner", "coder")

# 工程师写完代码后，进行条件判断：去执行工具 还是 结束？
workflow.add_conditional_edges("coder", route_after_coder)

# 工具执行完毕后（代码已落盘），直接进入沙盒运行测试
workflow.add_edge("tools", "sandbox")

# 沙盒运行完毕后，进行条件判断：结束 还是 回炉重造？
workflow.add_conditional_edges("sandbox", route_after_sandbox)

# 增加一条线：Reviewer 分析完之后，固定把控制权交给 Coder 去改代码
workflow.add_edge("reviewer", "coder")

# ==========================================
# 5. 编译并打包系统
# ==========================================
app = workflow.compile(
    checkpointer=checkpointer,
    # 💡 HITL 彩蛋：如果你想在沙盒运行前人工检查一下大模型写的代码，可以解开下面这行的注释
    # interrupt_before=["sandbox"]
)

# ==========================================
# 6. 测试运行入口
# ==========================================
if __name__ == "__main__":
    print("🚀 启动 nanoCursor ...")

    # 定义一个唯一的线程 ID (持久化记忆需要)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # 我们直接给一个带有 TDD (测试驱动开发) 要求的复杂指令
    user_prompt = """
    请帮我写一个 Python 函数，用于进行对列表进行二分查找。
    要求：
    1. 必须处理边界的情况。
    2. 写好代码后，必须在文件末尾加上至少 3 个 assert 测试用例来验证逻辑（包含正常值和异常值）。
    3. 将代码保存到你命名好的文件中。
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
        print(f"❌ 运行过程中发生框架级错误: {e}")

    print("\n✅ 运行流转结束！请去你的 workspace 目录下查看战果吧。")