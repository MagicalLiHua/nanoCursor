import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
from run import app

import uuid  # 加上这个引入

# 初始化 Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
# 为当前网页会话生成一个唯一的线程 ID
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

st.set_page_config(page_title="nanoCursor", page_icon="🤖", layout="wide")
st.title("💻 nanoCursor - 本地智能编程沙盒")

# 初始化 Session State (用于保存对话历史，防止页面刷新丢失)
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("⚙️ 控制面板")
    st.info("基于 LangGraph + Docker 构建的多智能体自动编程引擎。")

    # 清空对话按钮
    if st.button("🧹 清空对话历史", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.markdown("### 📊 Agent 内部状态")
    # 这里可以用来动态展示当前修改了哪些文件
    state_container = st.empty()

# 渲染历史对话气泡
for msg in st.session_state.messages:
    # 区分用户消息和 Agent 消息
    role = "user" if isinstance(msg, HumanMessage) else "assistant"
    with st.chat_message(role):
        st.markdown(msg.content)

if prompt := st.chat_input("输入你的需求，例如：'用 Python 写一个快排并进行沙盒测试'"):

    # 立即在前端显示用户输入
    with st.chat_message("user"):
        st.markdown(prompt)

    # 记录到状态中
    user_msg = HumanMessage(content=prompt)
    st.session_state.messages.append(user_msg)

    # 触发 Agent 后端
    with st.chat_message("assistant"):
        with st.spinner("🤖 Agent 正在思考与执行代码... (可能需要几十秒)"):
            try:
                # 构造初始状态交给 LangGraph
                initial_state = {"messages": st.session_state.messages}

                # 构造 config，把身份证（thread_id）递给 LangGraph
                config = {"configurable": {"thread_id": st.session_state.thread_id}}

                # 调用你的图，并把 config 传进去
                final_state = app.invoke(initial_state, config=config)

                # 提取最后一条输出消息
                final_messages = final_state.get("messages", [])
                if final_messages:
                    last_msg = final_messages[-1]
                    st.markdown(last_msg.content)
                    # 将结果存入历史记录
                    st.session_state.messages.append(last_msg)

                # 更新侧边栏的状态面板 (展示大模型的思维过程)
                with state_container.container():
                    active_files = final_state.get("active_files", [])
                    st.success(f"**目标文件:** {', '.join(active_files) if active_files else '无'}")
                    with st.expander("查看最新执行计划"):
                        st.write(final_state.get("current_plan", "暂无计划"))

            except Exception as e:
                st.error(f"⚠️ 系统执行出现异常: {str(e)}")