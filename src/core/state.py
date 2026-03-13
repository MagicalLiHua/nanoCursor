import operator
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from langgraph.checkpoint.redis import RedisSaver
from redis import Redis

# 初始化 Redis 作为 LangGraph 的 Checkpointer (实现时间回溯和 HITL 的基础)
# redis_client = Redis(host="", port=6379, db=0, decode_responses=False)
checkpointer = InMemorySaver()

# 2. 全局状态机定义 (AgentState)


class AgentState(TypedDict):
    """
    nanoCursor 的全局黑板 (Blackboard)。
    所有的 Agent 节点都在这里读取和写入数据。
    """
    # 1. 对话与执行历史 (LangGraph 的底层引擎)
    # 使用 add_messages 确保新消息是追加的，而不是覆盖原有的消息
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # 2. 工程上下文 (直接覆盖更新)
    current_plan: str  # Planner 节点生成的最新执行计划
    active_files: list[str]  # 当前正在修改的本地文件路径列表

    # 3. 沙盒与重试控制 (防止大模型陷入死循环)
    error_trace: str  # Sandbox 节点捕获的最新的报错信息 (stdout/stderr)
    retry_count: int  # 当前 Bug 修复的重试次数
    max_retries: int  # 允许的最大重试次数 (比如设为 3)