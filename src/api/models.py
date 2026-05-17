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
    is_connected: bool = False


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
    workspace_dir: str | None = Field(default=None, description="工作目录路径")
    messages: list[Message] | None = Field(default=None, description="对话历史消息列表，用于连续对话")
    conversation_id: str | None = Field(default=None, description="可选的 AgentHub 会话 ID")
    team: list[dict[str, Any]] | None = Field(default=None, description="本次运行的团队快照")
    execution_plan: dict[str, Any] | None = Field(default=None, description="本次运行的动态执行策略")


class RunResponse(BaseModel):
    """启动工作流的响应"""
    thread_id: str
    status: str


class ConversationCreateRequest(BaseModel):
    """创建 AgentHub 会话上下文"""
    prompt: str = Field(default="", max_length=2000)
    workspace_dir: str | None = Field(default=None, description="工作目录路径")


class ConversationRunRequest(BaseModel):
    """在会话上下文中启动一次隔离运行"""
    prompt: str = Field(..., min_length=1, max_length=4000)
    workspace_dir: str | None = Field(default=None, description="工作目录路径")


class ConversationTeamUpdateRequest(BaseModel):
    """更新会话内的可编辑 Agent 团队"""
    members: list[dict[str, Any]] = Field(default_factory=list)
    workspace_dir: str | None = Field(default=None, description="工作目录路径")


class ConversationTeamRecommendRequest(BaseModel):
    """重新生成会话智能组队建议"""
    prompt: str = Field(..., min_length=1, max_length=2000)
    workspace_dir: str | None = Field(default=None, description="工作目录路径")


class CapabilityRecommendRequest(BaseModel):
    """根据用户需求推荐 Agent 和能力包"""
    prompt: str = Field(..., min_length=1, max_length=1000)


class SkillImportRequest(BaseModel):
    """导入工作区自定义 Skill"""
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    content: str = Field(default="", max_length=8000)


class RunBlueprintRequest(BaseModel):
    """根据用户需求生成运行前执行蓝图"""
    prompt: str = Field(..., min_length=1, max_length=1000)


class ApprovalDecisionRequest(BaseModel):
    """用户对 Agent 计划的审批结果"""
    decision: str = Field(..., min_length=1, description="approved | revise | rejected")
    plan_id: str = Field(default="default-plan")
    comment: str = ""


class BenchmarkCase(BaseModel):
    """固定基准任务"""
    id: str
    title: str
    description: str
    prompt: str
    category: str
    difficulty: str
    acceptance_criteria: list[str]
    expected_artifacts: list[str]


class BenchmarkListResponse(BaseModel):
    """基准任务列表"""
    benchmarks: list[BenchmarkCase]


class BenchmarkRunRequest(BaseModel):
    """启动基准任务运行"""
    benchmark_id: str = Field(..., min_length=1)
    thread_id: str | None = None
    workspace_dir: str | None = None


class BenchmarkRunResponse(BaseModel):
    """基准任务启动响应"""
    thread_id: str
    status: str
    benchmark_id: str
    title: str


class AgentEvent(BaseModel):
    """AgentHub 前端消费的统一运行事件"""
    id: str
    thread_id: str
    type: str
    timestamp: float
    agent: str = "lead"
    title: str = ""
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RunSessionResponse(BaseModel):
    """AgentHub 运行会话状态"""
    thread_id: str
    workspace_dir: str
    status: str
    prompt: str = ""
    mode: str = "agenthub_delivery"
    created_at: float | None = None
    updated_at: float | None = None
    conversation_id: str | None = None
    team: list[dict[str, Any]] = Field(default_factory=list)
    execution_plan: dict[str, Any] = Field(default_factory=dict)
    lifecycle: dict[str, Any] = Field(default_factory=dict)


class RunHistoryItem(RunSessionResponse):
    """AgentHub 历史运行摘要"""
    event_count: int = 0
    changed_files_count: int = 0
    has_diff: bool = False
    has_report: bool = False
    last_event_type: str | None = None


class RunHistoryResponse(BaseModel):
    """历史运行列表响应"""
    runs: list[RunHistoryItem]


class QualityCheck(BaseModel):
    """单条质量门禁检查结果"""
    id: str
    label: str
    status: str
    severity: str
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class QualityGateResponse(BaseModel):
    """AgentHub 交付质量门禁结果"""
    thread_id: str
    workspace_dir: str
    status: str
    passed_count: int
    warning_count: int
    failed_count: int
    checks: list[QualityCheck]


class ScoreReason(BaseModel):
    """交付评分扣分原因"""
    id: str
    label: str
    impact: int
    detail: str = ""


class DeliveryScoreResponse(BaseModel):
    """AgentHub 交付评分结果"""
    thread_id: str
    workspace_dir: str
    score: int
    level: str
    quality_status: str
    passed_count: int
    warning_count: int
    failed_count: int
    reasons: list[ScoreReason]
    quality: QualityGateResponse


class RequirementTraceItem(BaseModel):
    """单条需求追踪覆盖结果"""
    id: str
    title: str
    description: str = ""
    status: str
    tasks: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class RequirementTraceabilityResponse(BaseModel):
    """AgentHub 需求追踪矩阵"""
    thread_id: str
    workspace_dir: str
    source: str
    total_count: int
    covered_count: int
    partial_count: int
    missing_count: int
    coverage_rate: float
    requirements: list[RequirementTraceItem]


class TeamAgentCreateRequest(BaseModel):
    """创建自定义 Agent 的请求"""
    name: str = Field(..., min_length=1, max_length=40)
    role: str = Field(..., min_length=1, max_length=40)
    goal: str = Field(default="", max_length=240)
    tools: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class PreferenceCreateRequest(BaseModel):
    """创建用户偏好记忆"""
    preference_type: str = Field(..., min_length=1, max_length=40)
    content: str = Field(..., min_length=1, max_length=500)
    importance: int = Field(default=8, ge=0, le=10)


class PreferenceMemoryItem(BaseModel):
    """偏好档案中的单条记忆"""
    id: str
    category: str
    content: str
    importance: int
    tags: list[str] = Field(default_factory=list)
    created_at: float | None = None
    last_accessed_at: float | None = None


class PreferenceBucket(BaseModel):
    """某类用户偏好"""
    id: str
    label: str
    description: str
    confidence: str
    memories: list[PreferenceMemoryItem] = Field(default_factory=list)


class MemoryProfileResponse(BaseModel):
    """AgentHub 用户偏好档案"""
    workspace_dir: str
    total_memories: int
    preference_count: int
    high_importance_count: int
    prompt_context: str
    buckets: list[PreferenceBucket]


class ArtifactItem(BaseModel):
    """单个交付物摘要"""
    id: str
    kind: str
    label: str
    status: str
    summary: str = ""
    path: str | None = None
    count: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ArtifactCenterResponse(BaseModel):
    """AgentHub 交付物中心"""
    thread_id: str
    workspace_dir: str
    status: str
    generated_at: float
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactItem]


class RecoveryPoint(BaseModel):
    """可恢复点摘要"""
    id: str
    kind: str
    label: str
    status: str
    timestamp: float | str | None = None
    path: str | None = None
    target_path: str | None = None
    size: int | None = None
    reason: str = ""
    detail: str = ""


class SafetyRisk(BaseModel):
    """安全风险或恢复建议"""
    id: str
    severity: str
    title: str
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class RecoveryAction(BaseModel):
    """失败后的推荐恢复动作"""
    id: str
    priority: str
    title: str
    detail: str = ""
    action_type: str
    target: str = ""
    enabled: bool = True


class RecoveryCenterResponse(BaseModel):
    """AgentHub 安全与恢复中心"""
    thread_id: str | None = None
    workspace_dir: str
    status: str
    generated_at: float
    summary: dict[str, Any] = Field(default_factory=dict)
    recovery_points: list[RecoveryPoint]
    risks: list[SafetyRisk]
    actions: list[RecoveryAction] = Field(default_factory=list)


class RollbackRequest(BaseModel):
    """从备份回滚文件"""
    backup_name: str = Field(..., min_length=1)
    target_path: str = Field(..., min_length=1)


class RollbackResponse(BaseModel):
    """文件回滚结果"""
    restored: bool
    backup_name: str
    target_path: str
    message: str


class RunEventsResponse(BaseModel):
    """历史事件响应"""
    events: list[AgentEvent]


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
