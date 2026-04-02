from typing import TypedDict, Annotated, Sequence, Optional
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages

# 初始化 LangGraph 的 Checkpointer
checkpointer = InMemorySaver()


class MemorySummary(TypedDict, total=False):
    """结构化的记忆摘要，用于替代冗长的原始对话"""
    original_request: str  # 用户原始需求 (永久保留)
    completed_steps: list[str]  # 已完成的步骤摘要
    key_decisions: list[str]  # 关键决策点
    file_operations: list[str]  # 文件操作摘要


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
    max_retries: int  # 允许的最大重试次数

    modification_log: list  # 记录 Coder 改了哪些文件的哪些内容

    # 4. 🌟 新增：分层上下文管理字段
    memory_summary: MemorySummary  # 结构化记忆摘要
    context_version: int  # 上下文版本号，每次压缩后递增
    file_signatures: dict[str, str]  # 文件签名缓存 {filepath: "函数A, 函数B | 上次修改时间"}

    # 5. 🌟 新增：Coder 步数控制
    coder_step_count: int  # Coder 工具调用步数计数器（每次微循环重置）
    max_coder_steps: int  # Coder 最大工具调用步数限制（默认 15）
