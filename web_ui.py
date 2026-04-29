"""
nanoCursor Web UI - 增强版 Streamlit 应用

主入口页面：工作台（对话 + workflow 可视化 + 实时状态）
子页面放在 pages/ 目录自动发现。
"""

import os
import sys
import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

# 确保项目根目录在 sys.path 中
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from run import app as graph_app
from src.core.config import WORKSPACE_DIR
from src.core.metrics import metrics as metrics_collector

st.set_page_config(
    page_title="nanoCursor 工作台",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Session State 初始化
# ============================================================
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "execution_log" not in st.session_state:
    st.session_state.execution_log = []
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "active_plan" not in st.session_state:
    st.session_state.active_plan = ""
if "active_files_list" not in st.session_state:
    st.session_state.active_files_list = []
if "retry_info" not in st.session_state:
    st.session_state.retry_info = {"count": 0, "max": 3}
if "error_trace" not in st.session_state:
    st.session_state.error_trace = ""

# ============================================================
# Sidebar
# ============================================================
def _count_workspace_files():
    try:
        count = 0
        for root, dirs, files in os.walk(WORKSPACE_DIR):
            dirs[:] = [d for d in dirs if d not in (".backups", ".snapshots")]
            count += len(files)
        return count
    except Exception:
        return 0

with st.sidebar:
    st.markdown("### 🤖 nanoCursor")
    st.caption("基于 LangGraph + Docker 的多智能体自动编程框架")
    st.divider()

    st.markdown("### 🔗 会话信息")
    st.code(f"Thread: {st.session_state.thread_id[:12]}...")
    status_text = "🟢 空闲" if not st.session_state.is_running else "🟡 运行中"
    st.caption(status_text)

    if st.button("🧹 清空对话", use_container_width=True):
        st.session_state.chat_messages = []
        st.session_state.execution_log = []
        st.session_state.active_plan = ""
        st.session_state.active_files_list = []
        st.session_state.retry_info = {"count": 0, "max": 3}
        st.session_state.error_trace = ""
        st.rerun()

    st.divider()

    st.markdown("### 📊 实时状态")
    if st.session_state.active_plan:
        with st.expander("📋 当前计划", expanded=True):
            st.caption(st.session_state.active_plan[:300])
    else:
        st.caption("暂无计划")

    if st.session_state.active_files_list:
        with st.expander("📂 目标文件", expanded=True):
            for f in st.session_state.active_files_list:
                st.caption(f"• {f}")
    else:
        st.caption("无目标文件")

    retry = st.session_state.retry_info
    if retry["count"] > 0:
        st.progress(retry["count"] / retry["max"])
        st.caption(f"重试: {retry['count']}/{retry['max']}")

    if st.session_state.error_trace:
        with st.expander("❌ 错误追踪", expanded=True):
            st.code(st.session_state.error_trace[:500], language="text")

    st.divider()

    st.markdown("### 📈 指标快照")
    summary = metrics_collector.dump_summary()
    llm = summary["llm"]
    tools_data = summary["tool_calls"]
    repair = summary["repair_cycles"]

    st.metric("LLM 调用", llm["total_calls"])
    st.metric("总 Token", llm["total_tokens"])
    st.metric("工具成功率", f"{tools_data['success_rate']:.0%}")
    if repair["total"] > 0:
        fixed = sum(1 for o in repair["outcomes"] if o["outcome"] == "fixed")
        failed = sum(1 for o in repair["outcomes"] if o["outcome"] == "still_failing")
        st.metric("修复循环", f"{repair['total']} (✅{fixed} ❌{failed})")
    else:
        st.metric("修复循环", 0)

    st.divider()
    st.caption(f"工作区文件: {_count_workspace_files()}")

# ============================================================
# 主区域
# ============================================================
st.title("nanoCursor 工作台")
st.caption("输入你的需求，智能体会自动规划、编码、测试")

chat_col, viz_col = st.columns([3, 2])

with chat_col:
    for msg in st.session_state.chat_messages:
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        with st.chat_message(role):
            st.markdown(msg.content)

    if prompt := st.chat_input("输入你的需求，例如：'用 Python 写一个快排并进行沙盒测试'"):
        st.session_state.chat_messages.append(HumanMessage(content=prompt))
        st.session_state.execution_log = []
        st.session_state.is_running = True
        st.session_state.active_plan = ""
        st.session_state.active_files_list = []
        st.session_state.retry_info = {"count": 0, "max": 3}
        st.session_state.error_trace = ""
        st.rerun()

    if len(st.session_state.chat_messages) > 0:
        last = st.session_state.chat_messages[-1]
        is_last_human = isinstance(last, HumanMessage)
        has_response = any(
            isinstance(m, AIMessage) for m in st.session_state.chat_messages[
                st.session_state.chat_messages.index(last):
            ][1:]
        ) if is_last_human else True

        if is_last_human and not has_response:
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            initial_state = {"messages": list(st.session_state.chat_messages)}

            with st.status("🤖 多智能体工作流执行中...", expanded=True) as status:
                exec_log = []
                try:
                    for event in graph_app.stream(initial_state, config=config, stream_mode="updates"):
                        for node_name, node_state in event.items():
                            if node_name == "planner":
                                plan = node_state.get("current_plan", "")
                                if plan:
                                    st.session_state.active_plan = plan
                                    st.info(f"**Planner 完成规划:**\n{plan[:200]}...")
                                exec_log.append(("🧠 Planner", "completed", "生成了开发计划"))

                            elif node_name == "planner_tools":
                                st.write("🛠️ **Planner** 调用只读工具探索工作区...")
                                exec_log.append(("🛠️ Planner Tools", "info", "读取文件/列出目录"))

                            elif node_name == "coder":
                                if node_state.get("messages"):
                                    content = node_state["messages"][-1].content
                                    st.caption(f"💻 **Coder:** {content[:200]}...")
                                exec_log.append(("💻 Coder", "completed", "代码/工具调用"))

                            elif node_name == "coder_step_counter":
                                step = node_state.get("coder_step_count", 0)
                                st.caption(f"🔢 Coder 第 {step} 步工具调用")
                                exec_log.append(("🔢 Coder Step", "info", f"步数: {step}"))

                            elif node_name == "coder_tools":
                                st.write("🛠️ **Coder** 调用文件工具...")
                                exec_log.append(("🛠️ Coder Tools", "info", "读写文件"))

                            elif node_name == "sandbox":
                                error = node_state.get("error_trace", "")
                                if error:
                                    st.session_state.error_trace = error
                                    st.error(f"**Sandbox 测试失败:**\n```text\n{error[:500]}\n```")
                                    exec_log.append(("📦 Sandbox", "failed", "测试未通过"))
                                else:
                                    st.session_state.error_trace = ""
                                    st.success("📦 **Sandbox** 测试通过！")
                                    exec_log.append(("📦 Sandbox", "completed", "测试通过"))
                                st.session_state.retry_info["count"] = node_state.get("retry_count", 0)
                                st.session_state.retry_info["max"] = node_state.get("max_retries", 3)

                            elif node_name == "reviewer":
                                if node_state.get("messages"):
                                    content = node_state["messages"][-1].content
                                    st.warning(f"**Reviewer 诊断:**\n{content[:300]}...")
                                exec_log.append(("🧐 Reviewer", "warning", "分析错误并给出修复建议"))

                    status.update(label="✅ 工作流执行闭环完成！", state="complete", expanded=False)
                    st.session_state.execution_log = exec_log

                except Exception as e:
                    status.update(label=f"❌ 执行出错: {e}", state="error", expanded=False)
                    st.error(str(e))
                    st.session_state.execution_log = exec_log
                finally:
                    st.session_state.is_running = False
                    try:
                        final_state = graph_app.get_state(config).values
                        final_messages = final_state.get("messages", [])
                        if final_messages:
                            last_msg = final_messages[-1]
                            if not any(m.content == getattr(last_msg, 'content', '') for m in st.session_state.chat_messages[1:]):
                                st.session_state.chat_messages.append(last_msg)
                        st.session_state.active_plan = final_state.get("current_plan", st.session_state.active_plan)
                        st.session_state.active_files_list = final_state.get("active_files", [])
                    except Exception:
                        pass
                    st.rerun()

with viz_col:
    st.markdown("### 执行轨迹")

    if st.session_state.execution_log:
        for node, status, detail in st.session_state.execution_log:
            icon = {"completed": "✅", "failed": "❌", "warning": "⚠️", "info": "ℹ️"}.get(status, "ℹ️")
            st.markdown(f"{icon} **{node}** — {detail}")
    else:
        st.caption("暂无执行记录")

    st.divider()
    st.markdown("### 工作流图")

    executed_names = set(e[0] for e in st.session_state.execution_log) if st.session_state.execution_log else set()

    def clr(name):
        n = name.lower()
        for e in executed_names:
            if n in e.lower() or e.lower() in n:
                return "#4ade80"
        return "#e5e7eb"

    mermaid = """```mermaid
flowchart TD
    START((开始)) --> planner[Planner]
    planner -- 工具调用 --> planner_tools[Planner Tools]
    planner_tools --> planner
    planner -- 无调用 --> coder[Coder]
    coder -- 工具调用 --> step_counter[Step Counter]
    step_counter --> coder_tools[Coder Tools]
    coder_tools --> coder
    coder -- 无调用 --> sandbox[Sandbox]
    sandbox -- 通过 --> END_NODE(结束)
    sandbox -- 失败 --> reviewer[Reviewer]
    reviewer --> coder
    sandbox -- 超限 --> END_NODE

    style planner fill:""" + clr("planner") + """,stroke:#333,stroke-width:2px
    style planner_tools fill:""" + clr("planner_tools") + """,stroke:#333,stroke-width:2px
    style coder fill:""" + clr("coder") + """,stroke:#333,stroke-width:2px
    style step_counter fill:""" + clr("step_counter") + """,stroke:#333,stroke-width:2px
    style coder_tools fill:""" + clr("coder_tools") + """,stroke:#333,stroke-width:2px
    style sandbox fill:""" + clr("sandbox") + """,stroke:#333,stroke-width:2px
    style reviewer fill:""" + clr("reviewer") + """,stroke:#333,stroke-width:2px
    style END_NODE fill:#e5e7eb,stroke:#333
```"""
    st.markdown(mermaid)
