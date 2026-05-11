"""
AgentState 定义 - nanoCursor 的全局黑板 (Blackboard)

移除了 LangGraph checkpointer 相关代码。
状态持久化现在由 src/core/checkpoint.py 的 CheckpointManager 处理。
"""

from typing import TypedDict


class MemorySummary(TypedDict, total=False):
    """结构化的记忆摘要，用于替代冗长的原始对话"""
    original_request: str  # 用户原始需求 (永久保留)
    completed_steps: list[str]  # 已完成的步骤摘要
    key_decisions: list[str]  # 关键决策点
    file_operations: list[str]  # 文件操作摘要


class AgentState(TypedDict):
    """
    nanoCursor 的全局黑板 (Blackboard) - Supervisor 驱动的多 Agent 架构。
    所有的 Agent 节点都在这里读取和写入数据。
    """
    # --- 对话与执行历史 ---
    messages: list  # 对话消息列表，各 agent 负责 append

    # --- Task Pool (LLM 驱动编排的核心) ---
    task_pool: dict  # {"tasks": [...], "completed": [...], "failed": [...]}
    completed_tasks: list[str]  # 成功完成的任务 ID 列表
    failed_tasks: list[str]  # 失败的任务 ID 列表
    current_task_id: str | None  # 当前正在执行的任务 ID

    # --- 计划 (来自 Planner) ---
    execution_plan: dict | None  # ExecutionPlan schema 序列化
    current_plan: str  # 人类可读的计划文本
    active_files: list[str]  # 当前正在修改的本地文件路径列表

    # --- 错误处理 ---
    error_trace: str  # Sandbox 捕获的最新报错信息
    diagnosis_history: list[dict]  # DiagnosisSchema 条目，多轮自我纠正积累
    retry_count: int  # 当前 Bug 修复重试次数
    max_retries: int  # 允许的最大重试次数

    # --- 验证 ---
    verification_result: dict | None  # VerificationResult serialized
    verification_passed: bool | None

    # --- 上下文管理 ---
    memory_summary: MemorySummary
    context_version: int
    file_signatures: dict[str, str]
    modification_log: list

    # --- 工作流控制 ---
    cancelled: bool  # 用户取消标志，每个节点执行前检查

    # --- Supervisor 专用 ---
    last_action: str  # Supervisor 上一次决定：planner/coder/reviewer/verifier/sandbox/END
    step_budget: int  # 剩余步数预算
    max_steps: int  # 全局最大步数预算

    # --- 向后兼容字段 ---
    coder_step_count: int  # 旧版 Coder 步数计数器
    max_coder_steps: int  # 旧版最大步数限制
    reviewer_diagnosis: dict | None  # 旧版 Diagnosis 输出

    # --- Todo List (用户任务清单) ---
    todos: list  # in-session working copy of TodoItem dicts

    # --- Sub-Agent (后台子任务) ---
    active_subagents: dict  # subagent_id -> serialized SubAgent
    subagent_results: dict  # subagent_id -> result string for Supervisor to consume

    # --- Memory (跨会话持久化记忆) ---
    working_memory: list  # in-session MemoryEntry dicts (short-term)
    memory_retrieved: bool  # flag: long-term memory loaded this session


class WorkflowCancelledError(Exception):
    """工作流被用户取消时抛出"""
    pass


def check_cancelled(state: AgentState) -> None:
    """
    检查工作流是否已被用户取消。
    每个节点开始时调用，若已取消则抛出 WorkflowCancelledError。
    """
    if state.get("cancelled", False):
        raise WorkflowCancelledError("工作流已被用户取消")
