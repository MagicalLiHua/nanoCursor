"""
nanoCursor API Pydantic Models

为 API 层提供类型化的请求/响应模型，确保：
1. 输入验证的精确性
2. 输出的类型安全性
3. 更好的 IDE 自动补全和文档
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ============================================================
# 通用类型定义
# ============================================================


class Message(BaseModel):
    """聊天消息结构"""
    role: str
    content: str


class LLMProviderStatus(BaseModel):
    """LLM 提供商状态"""
    has_key: bool
    model: str
    base_url: str | None = None


class SystemConfig(BaseModel):
    """系统配置"""
    workspace_dir: str
    sandbox_image: str
    sandbox_mem_limit: str
    sandbox_timeout: int
    max_coder_steps: int
    max_planner_steps: int
    context_max_tokens: int


class EnvVar(BaseModel):
    """环境变量（脱敏后）"""
    name: str
    value: str
    is_sensitive: bool
    is_set: bool


# ============================================================
# 文件相关模型
# ============================================================


class FileEntry(BaseModel):
    """文件/目录条目"""
    path: str
    is_dir: bool
    size: int
    mtime: float | None = None


class FileListResponse(BaseModel):
    """文件列表响应"""
    files: list[FileEntry]


class FileContentResponse(BaseModel):
    """文件内容响应"""
    content: str
    size: int
    lines: int
    mtime: float
    lang: str


# ============================================================
# 工作流相关模型
# ============================================================


class RunRequest(BaseModel):
    """启动工作流的请求"""
    prompt: str = Field(..., min_length=1, description="用户输入的需求描述")
    thread_id: str | None = Field(default=None, description="可选的已有线程 ID，用于继续对话")


class RunResponse(BaseModel):
    """启动工作流的响应"""
    thread_id: str
    status: str


class CancelResponse(BaseModel):
    """取消工作流的响应"""
    cancelled: bool
    thread_id: str


class NodeUpdateData(BaseModel):
    """节点更新数据的通用结构"""
    current_plan: str | None = None
    content: str | None = None
    coder_step_count: int | None = None
    error_trace: str | None = None
    retry_count: int | None = None
    max_retries: int | None = None
    metrics: dict[str, Any] | None = None


class WorkflowDoneData(BaseModel):
    """工作流完成数据"""
    status: str  # "completed" or "cancelled"


class WorkflowErrorData(BaseModel):
    """工作流错误数据"""
    message: str


# ============================================================
# 指标相关模型
# ============================================================


class MetricsSummary(BaseModel):
    """指标摘要（扁平结构）"""
    total_llm_calls: int = 0
    total_tokens: int = 0
    llm_latency_avg: float = 0.0
    tool_calls: int = 0
    tool_successes: int = 0
    tool_failures: int = 0
    tool_success_rate: float = 0.0
    repair_cycles: int = 0
    repair_cycles_recovered: int = 0
    last_updated: str | None = None


class MetricsLLMData(BaseModel):
    """LLM 指标数据（嵌套结构，保持向后兼容）"""
    total_calls: int = 0
    total_tokens: int = 0
    avg_tokens_per_call: float = 0.0
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = 0.0


class MetricsToolData(BaseModel):
    """工具调用指标数据（嵌套结构，保持向后兼容）"""
    total: int = 0
    successes: int = 0
    failures: int = 0
    success_rate: float = 0.0
    failure_reasons: list[str] = []


class MetricsRepairData(BaseModel):
    """修复循环指标数据（嵌套结构，保持向后兼容）"""
    total: int = 0
    outcomes: list[dict[str, Any]] = []


class MetricsCurrentResponse(BaseModel):
    """指标当前数据（兼容新旧格式）"""
    # 新扁平字段
    total_llm_calls: int = 0
    total_tokens: int = 0
    llm_latency_avg: float = 0.0
    tool_calls: int = 0
    tool_successes: int = 0
    tool_failures: int = 0
    tool_success_rate: float = 0.0
    repair_cycles: int = 0
    repair_cycles_recovered: int = 0
    last_updated: str | None = None
    # 旧嵌套字段（向后兼容）
    llm: MetricsLLMData = Field(default_factory=MetricsLLMData)
    tool_calls_detail: MetricsToolData = Field(default_factory=MetricsToolData)
    repair_cycles_detail: MetricsRepairData = Field(default_factory=MetricsRepairData)


class MetricsResponse(BaseModel):
    """指标响应"""
    current: MetricsCurrentResponse
    historical: list[dict[str, Any]]


# ============================================================
# 配置相关模型
# ============================================================


class ConfigResponse(BaseModel):
    """配置信息响应"""
    llm_providers: dict[str, LLMProviderStatus]
    system: SystemConfig
    env_vars: list[EnvVar]


# ============================================================
# 快照相关模型
# ============================================================


class SnapshotEntry(BaseModel):
    """快照条目"""
    id: str
    timestamp: str
    reason: str
    active_files: list[str] = []
    active_files_count: int


class SnapshotListResponse(BaseModel):
    """快照列表响应"""
    snapshots: list[SnapshotEntry]


class SnapshotMetadata(BaseModel):
    """快照元数据"""
    timestamp: str
    reason: str
    active_files: list[str]


class CodeFile(BaseModel):
    """代码文件内容"""
    path: str
    content: str


class SnapshotDetailResponse(BaseModel):
    """快照详情响应"""
    metadata: SnapshotMetadata
    conversation_summary: str | dict[str, Any] = ""
    code_files: list[CodeFile]


# ============================================================
# 备份相关模型
# ============================================================


class BackupEntry(BaseModel):
    """备份文件条目"""
    name: str
    size: int
    mtime: float


class BackupListResponse(BaseModel):
    """备份列表响应"""
    backups: list[BackupEntry]


class BackupContentResponse(BaseModel):
    """备份内容响应"""
    content: str
    size: int
    mtime: float


# ============================================================
# 状态相关模型
# ============================================================


class AgentStateResponse(BaseModel):
    """Agent 状态响应（简化版）"""
    messages: list[Message] = []
    current_plan: str | None = None
    error_trace: str | None = None
    coder_step_count: int = 0
    retry_count: int = 0
    max_retries: int = 3
    cancelled: bool = False
    # 其他字段作为原始值传递
    extra: dict[str, Any] = Field(default_factory=dict)
