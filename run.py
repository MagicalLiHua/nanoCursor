import uuid
import json
import os
import asyncio
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from src.core.state import AgentState, checkpointer
from src.core.routing import route_after_planner, route_after_coder
from src.agents.Planner import planner_node
from src.agents.Coder import coder_node
from src.agents.Sandbox import sandbox_node
from src.agents.Reviewer import reviewer_node
from src.tools.file_tools import tools, read_file, list_directory, planner_tools
from src.core.logger import setup_logger
from src.core.metrics import metrics
from src.core.recovery import create_workspace_snapshot

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
workflow.add_node("planner_tools", ToolNode(planner_tools))
workflow.add_node("coder_tools", ToolNode(tools))

workflow.add_node("sandbox", sandbox_node)
workflow.add_node("reviewer", reviewer_node)


# ==========================================
# 3. 路由逻辑 (Conditional Edges)
# route_after_planner, route_after_coder 已抽离到 src/core/routing.py
# route_after_sandbox 包含 side effects（快照/指标打印），保留在此
# ==========================================

def route_after_sandbox(state: AgentState):
    """大脑的中枢：判断测试是否通过，是否需要循环修 Bug
    注意：repair_cycle_start 的计数已移至 sandbox_node 内部，
    避免路由回调被多次调用导致重复计数。
    """
    error_trace = state.get("error_trace", "")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    # 1. 成功分支：没有报错信息
    if not error_trace:
        print("[Router] 测试通过！任务完美闭环。")
        metrics.record_repair_cycle_outcome("fixed")
        _print_metrics_summary()
        return END

    # 2. 失败分支：超出最大重试次数 (兜底机制)
    if retry_count >= max_retries:
        print(f"[Router] 达到最大重试次数 ({max_retries})，大模型尽力了。")
        # 记录修复循环失败
        metrics.record_repair_cycle_outcome("still_failing", error_trace[:300])
        # 错误恢复：在结束前保存工作区快照
        snapshot_dir = create_workspace_snapshot(state, reason="max_retries_reached")
        if snapshot_dir:
            print(f"[Recovery] 工作区已保存至快照目录: {snapshot_dir}")
            metadata_path = os.path.join(snapshot_dir, "metadata.json")
            if os.path.exists(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    print(f"[Recovery] 已修复的文件 ({len(meta.get('active_files', []))} 个): {', '.join(meta.get('active_files', []))}")
        _print_metrics_summary()
        return END

    # 3. 循环分支：带着报错信息，先交给 Reviewer 分析！
    print(f"[Router] 发现 Bug，交由 Reviewer 分析 (已重试 {retry_count} 次)...")
    metrics.record_repair_cycle_outcome("still_failing", error_trace[:300])
    return "reviewer"


def _print_metrics_summary():
    """打印当前运行的指标摘要。"""
    print("\n" + metrics.render_summary())


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
async def main():
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
        "retry_count": 0,
        "max_coder_steps": 15,
    }

    # 开始异步流式运行我们的图
    try:
        async for event in app.astream(initial_state, config=config, stream_mode="values"):
            # 由于我们在每个节点里都写了 print() 来打印状态，这里不用做额外处理
            pass
    except Exception as e:
        print(f"运行过程中发生框架级错误: {e}")

    print("\n运行流转结束！请去你的 workspace 目录下查看战果吧。")


if __name__ == "__main__":
    asyncio.run(main())
