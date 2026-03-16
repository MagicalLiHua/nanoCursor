import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
from run import app
import uuid

# 初始化 Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
# 为当前网页会话生成一个唯一的线程 ID
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

st.set_page_config(page_title="nanoCursor", page_icon="🤖", layout="wide")
st.title("💻 nanoCursor - 本地智能编程沙盒")

with st.sidebar:
    st.header("⚙️ 控制面板")
    st.info("基于 LangGraph + Docker 构建的多智能体自动编程引擎。")

    # 清空对话按钮
    if st.button("🧹 清空对话历史", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.markdown("### 📊 Agent 内部状态")
    state_container = st.empty()

# 渲染历史对话气泡
for msg in st.session_state.messages:
    role = "user" if isinstance(msg, HumanMessage) else "assistant"
    with st.chat_message(role):
        st.markdown(msg.content)

if prompt := st.chat_input("输入你的需求，例如：'用 Python 写一个快排并进行沙盒测试'"):

    with st.chat_message("user"):
        st.markdown(prompt)

    user_msg = HumanMessage(content=prompt)
    st.session_state.messages.append(user_msg)

    with st.chat_message("assistant"):
        # ==========================================
        # 🌟 核心修改：使用 st.status 和节点流式监听替代 Spinner
        # ==========================================
        with st.status("🤖 正在调度多智能体工作流...", expanded=True) as status:
            initial_state = {"messages": st.session_state.messages}
            config = {"configurable": {"thread_id": st.session_state.thread_id}}

            # 使用 stream_mode="updates" 实时捕获每个节点的增量执行结果
            for event in app.stream(initial_state, config=config, stream_mode="updates"):
                # event 包含了刚刚执行完毕的节点名称和它返回的状态
                for node_name, node_state in event.items():

                    if node_name == "planner":
                        st.write("🧠 **Planner (规划师) 完成探索与规划:**")
                        st.info(node_state.get("current_plan", "生成计划中..."))

                    elif node_name == "coder":
                        st.write("💻 **Coder (工程师) 执行了代码操作:**")
                        if "messages" in node_state and node_state["messages"]:
                            st.caption(node_state["messages"][-1].content)

                    elif node_name == "sandbox":
                        st.write("📦 **Sandbox (沙盒) 运行完毕:**")
                        error = node_state.get("error_trace", "")
                        if error:
                            st.error(f"发现报错，准备交由 Reviewer 分析:\n```text\n{error}\n```")
                        else:
                            st.success("🎉 沙盒测试完美通过！")

                    elif node_name == "reviewer":
                        st.write("🧐 **Reviewer (审查员) 提出修复建议:**")
                        if "messages" in node_state and node_state["messages"]:
                            st.warning(node_state["messages"][-1].content)

                    elif "tools" in node_name:
                        st.write(f"🛠️ Agent 调用了本地文件工具... ({node_name})")

            # 当循环结束，说明整个图 (Graph) 走到了 END
            status.update(label="✅ 工作流执行闭环完成！", state="complete", expanded=False)

        # ==========================================
        # 获取最终状态并渲染总结
        # ==========================================
        # 从 checkpointer 获取图的最终完整状态
        final_state = app.get_state(config).values

        # 提取最后一条输出消息进行展示
        final_messages = final_state.get("messages", [])
        if final_messages:
            last_msg = final_messages[-1]
            st.markdown(last_msg.content)
            st.session_state.messages.append(last_msg)

        # 动态更新侧边栏的状态面板
        with state_container.container():
            active_files = final_state.get("active_files", [])
            st.success(f"**目标文件:** {', '.join(active_files) if active_files else '无'}")
            with st.expander("查看最新执行计划"):
                st.write(final_state.get("current_plan", "暂无计划"))