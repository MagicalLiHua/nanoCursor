"""
nanoCursor API Pydantic Models

为 API 层提供类型化的请求/响应模型，确保：
1. 输入验证的精确性
2. 输出的类型安全性
3. 更好的 IDE 自动补全和文档
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ============================================================
# 通用类型定义
# ============================================================


class Message(BaseModel):
    """聊天消息结构"""
    role: str
    content: str


IntentRoute = Literal[
    "direct_answer",
    "read_only",
    "small_edit",
    "feature_delivery",
    "debug_fix",
    "test_only",
    "review_only",
    "risky_operation",
    "clarification_needed",
]


class IntentDecision(BaseModel):
    """Structured pre-run routing decision.

    ``route`` describes the product-level user intent. ``execution_route`` keeps
    compatibility with the existing runtime entrypoints.
    """

    route: IntentRoute
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_workspace_read: bool = False
    requires_workspace_write: bool = False
    requires_shell: bool = False
    requires_approval: bool = False
    requires_execution: bool = False
    suggested_agents: list[str] = Field(default_factory=lambda: ["Lead"])
    rationale: str = ""
    missing_information: list[str] = Field(default_factory=list)

    intent: str = ""
    level: str = "simple"
    complexity: str = "simple"
    strategy: str = "analysis_only"
    execution_route: Literal["lead_direct_reply", "agenthub_delivery"] = "lead_direct_reply"
    signals: list[str] = Field(default_factory=list)
    indicators: list[str] = Field(default_factory=list)
    source: str = "deterministic_guard"
    risk_level: Literal["low", "medium", "high"] = "low"
    risk_reasons: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    context_requirements: dict[str, Any] = Field(default_factory=dict)
    tool_permissions: dict[str, str] = Field(default_factory=dict)
    suggested_agent_specs: list[dict[str, Any]] = Field(default_factory=list)
    guard_hits: list[str] = Field(default_factory=list)
    normalized_from: str = ""
    raw_decision: dict[str, Any] = Field(default_factory=dict)


class AgentIntentSpec(BaseModel):
    """Suggested runtime Agent from a structured intent decision."""

    role: str
    mode: Literal["permanent", "temporary"] = "temporary"
    goal: str = ""
    permissions: list[str] = Field(default_factory=list)
    exit_condition: str = ""


class IntentDecisionV3(IntentDecision):
    """Intent Router V3 contract.

    Extends the stable V2 response with structured Agent, context, risk and
    tool-permission metadata. Existing API consumers can keep reading the V2
    fields while newer runtime paths consume the richer fields.
    """

    suggested_agent_specs: list[AgentIntentSpec] = Field(default_factory=list)


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
    conversation_id: str | None = Field(default=None, description="可选的 nanoCursor 会话 ID")
    team: list[dict[str, Any]] | None = Field(default=None, description="本次运行的团队快照")
    execution_plan: dict[str, Any] | None = Field(default=None, description="本次运行的动态执行策略")


class RunResponse(BaseModel):
    """启动工作流的响应"""
    thread_id: str
    status: str


class RetryRunRequest(BaseModel):
    """重试运行的请求"""
    retry_mode: str = Field(default="full", description="full | failed_stage")
    failure_id: str | None = Field(default=None, description="可选的失败记录 ID")
    instruction: str = Field(default="", max_length=2000, description="用户补充的重试指令")


class IntentCorrectionRequest(BaseModel):
    """Runtime correction for a persisted run intent decision."""
    route: IntentRoute
    complexity: Literal["simple", "small_code", "medium", "high_risk"] | None = None
    reason: str = Field(..., min_length=1, max_length=1000)
    evidence: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="runtime_correction", max_length=120)


class TaskResultRequest(BaseModel):
    """Apply a mutable task-board task result."""
    status: str = Field(..., description="passed | failed | blocked | skipped")
    summary: str = Field(default="", max_length=2000)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    failure_category: str | None = Field(default=None)
    retryable: bool = True


GraphNodeResultRequest = TaskResultRequest


class RunStateNodePatch(BaseModel):
    """Add or update one task-board task.

    The class name is kept stable for existing OpenAPI/client compatibility;
    new code should treat each item as a task-board task, not a workflow node.
    """
    id: str = Field(..., min_length=1, max_length=120)
    type: str = Field(default="analysis")
    title: str = Field(..., min_length=1, max_length=200)
    goal: str = Field(default="", max_length=1000)
    agent_role: str = Field(default="lead", max_length=80)
    dependencies: list[str] = Field(default_factory=list)
    can_parallel: bool = False
    writes_files: bool = False
    resource_locks: list[str] = Field(default_factory=list)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    context_policy: dict[str, Any] = Field(default_factory=dict)


class RunStatePatchRequest(BaseModel):
    """Mutable run-state patch emitted by Lead Agent loop or UI controls."""
    reason: str = Field(default="agent_loop_update", max_length=500)
    add_or_update_nodes: list[RunStateNodePatch] = Field(default_factory=list)
    add_or_update_tasks: list[RunStateNodePatch] = Field(default_factory=list)
    remove_nodes: list[str] = Field(default_factory=list)
    remove_tasks: list[str] = Field(default_factory=list)
    connect: list[dict[str, str]] = Field(default_factory=list)
    disconnect: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


RunStateTaskPatch = RunStateNodePatch


class LoopActionCheckRequest(BaseModel):
    """Dry-run a structured Lead loop action without mutating the ledger."""
    action: dict[str, Any] = Field(default_factory=dict)


class LoopStepRequest(BaseModel):
    """Run one Agent Loop controller step."""
    action: dict[str, Any] = Field(default_factory=dict)
    commit: bool = False
    auto_repair: bool = True
    execute_tools: bool = False


class ConversationCreateRequest(BaseModel):
    """创建 nanoCursor 会话上下文"""
    prompt: str = Field(default="", max_length=2000)
    workspace_dir: str | None = Field(default=None, description="工作目录路径")


class ConversationRunRequest(BaseModel):
    """在会话上下文中启动一次隔离运行"""
    prompt: str = Field(..., min_length=1, max_length=4000)
    workspace_dir: str | None = Field(default=None, description="工作目录路径")
    messages: list[Message] | None = Field(default=None, description="同一会话内的最近消息，用于连续对话")


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
    enabled: bool | None = None
    skill_json: dict[str, Any] = Field(default_factory=dict)


class SkillPreviewRequest(BaseModel):
    """预览本次请求会选择哪些 Skills"""
    prompt: str = Field(..., min_length=1, max_length=2000)
    team: list[dict[str, Any]] = Field(default_factory=list)


class SkillEnabledRequest(BaseModel):
    """Skill 启停请求"""
    enabled: bool = True


class GitHubSkillImportPreviewRequest(BaseModel):
    """GitHub 静态 Skill 导入预览"""
    repo_url: str = Field(..., min_length=1, max_length=500)
    ref: str = Field(default="", max_length=120)
    path: str = Field(default="", max_length=300)
    token: str = Field(default="", max_length=500)


class GitHubSkillImportRequest(GitHubSkillImportPreviewRequest):
    """确认导入 GitHub 静态 Skill"""
    candidate_id: str = Field(default="", max_length=120)
    enabled: bool | None = None


class GitHubSkillUpdateRequest(BaseModel):
    """GitHub Skill 更新检查/预览请求"""
    ref: str = Field(default="", max_length=120)
    token: str = Field(default="", max_length=500)


class GitHubSkillUpdateApplyRequest(GitHubSkillUpdateRequest):
    """确认应用 GitHub Skill 更新"""
    confirmed: bool = False
    enabled: bool | None = None


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


class RealTaskBenchmarkRunRequest(BaseModel):
    """运行真实任务静态 benchmark"""
    case_ids: list[str] = Field(default_factory=list)
    persist: bool = True


class BenchmarkRunResponse(BaseModel):
    """基准任务启动响应"""
    thread_id: str
    status: str
    benchmark_id: str
    title: str


class AgentEvent(BaseModel):
    """nanoCursor 前端消费的统一运行事件"""
    id: str
    thread_id: str
    type: str
    timestamp: float
    agent: str = "lead"
    title: str = ""
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RunSnapshotRun(BaseModel):
    """Run snapshot: durable run metadata."""
    thread_id: str
    status: str = "missing"
    mode: str = "agenthub_delivery"
    prompt: str = ""
    created_at: float | None = None
    updated_at: float | None = None
    strategy: str = ""
    is_active: bool = False


class RunSnapshotWorkspace(BaseModel):
    """Run snapshot: workspace/environment summary."""
    path: str
    name: str = ""
    git_branch: str = ""
    dirty: bool = False
    is_git_repo: bool = False


class RunSnapshotConversation(BaseModel):
    """Run snapshot: compact conversation state."""
    conversation_id: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class RunSnapshotActivity(BaseModel):
    """Run snapshot: current user-facing activity."""
    current_agent: str = ""
    current_action: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)


class RunSnapshotChanges(BaseModel):
    """Run snapshot: change summary."""
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "none"


class RunSnapshotQuality(BaseModel):
    """Run snapshot: quality and risk summary."""
    status: str = "unknown"
    score: int | None = None
    gates: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)


class RunSnapshotCapabilities(BaseModel):
    """Run snapshot: Skills and MCP capabilities selected for this run."""
    selected_skills: list[dict[str, Any]] = Field(default_factory=list)
    omitted_skills: list[dict[str, Any]] = Field(default_factory=list)
    mcp_plan: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class RunSnapshotRouting(BaseModel):
    """Run snapshot: structured routing decision for this run."""
    decision: dict[str, Any] = Field(default_factory=dict)


class RunSnapshot(BaseModel):
    """Frontend-facing aggregate for one run.

    The snapshot is intentionally read-only: building it must not create a task
    board, approvals, reports, or other run artifacts as a side effect.
    """
    run: RunSnapshotRun
    workspace: RunSnapshotWorkspace
    conversation: RunSnapshotConversation = Field(default_factory=RunSnapshotConversation)
    activity: RunSnapshotActivity = Field(default_factory=RunSnapshotActivity)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    changes: RunSnapshotChanges = Field(default_factory=RunSnapshotChanges)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    quality: RunSnapshotQuality = Field(default_factory=RunSnapshotQuality)
    capabilities: RunSnapshotCapabilities = Field(default_factory=RunSnapshotCapabilities)
    routing: RunSnapshotRouting = Field(default_factory=RunSnapshotRouting)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    outcome: dict[str, Any] = Field(default_factory=dict)


class RunSessionResponse(BaseModel):
    """nanoCursor 运行会话状态"""
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
    """nanoCursor 历史运行摘要"""
    event_count: int = 0
    changed_files_count: int = 0
    has_diff: bool = False
    has_report: bool = False
    last_event_type: str | None = None
    is_active: bool = False
    is_write_mode: bool = False
    source: str = "history"


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
    """nanoCursor 交付质量门禁结果"""
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
    """nanoCursor 交付评分结果"""
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
    """nanoCursor 需求追踪矩阵"""
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
    lifetime: str = Field(default="permanent", description="permanent 或 temporary")
    thread_id: str | None = Field(default=None, description="创建临时 Agent 时所属运行 ID")
    mcp_servers: list[str] = Field(default_factory=list)
    blocked_capabilities: list[str] = Field(default_factory=list)
    risk_level: str = Field(default="medium")
    task_scope: dict[str, Any] | None = None
    expected_output: dict[str, Any] | None = None
    ttl_seconds: int | None = None


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
    """nanoCursor 用户偏好档案"""
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
    """nanoCursor 交付物中心"""
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
    """nanoCursor 安全与恢复中心"""
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
    confirmed: bool = False


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


# ============================================================
# MCP 配置
# ============================================================


class McpServerItem(BaseModel):
    """MCP server 配置项"""
    id: str
    name: str
    status: str
    source: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env_keys: list[str] = Field(default_factory=list)
    setup_hint: str = ""
    last_used_run_id: str | None = None


class McpConfigResponse(BaseModel):
    """MCP 配置详情响应"""
    workspace_dir: str
    config_paths: list[str] = Field(default_factory=list)
    servers: list[McpServerItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class McpValidateRequest(BaseModel):
    """MCP 配置验证请求"""
    server_id: str | None = None


# ============================================================
# Skill 详情与编辑
# ============================================================


class SkillDetailResponse(BaseModel):
    """Skill 详情"""
    id: str
    name: str
    status: str
    source: str
    path: str
    description: str = ""
    content: str
    agents: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    last_used_run_id: str | None = None


class SkillUpdateRequest(BaseModel):
    """Skill 内容更新请求"""
    content: str = Field(..., min_length=1)


# ============================================================
# 恢复动作执行
# ============================================================


class RecoveryActionRequest(BaseModel):
    """恢复动作执行请求"""
    action_id: str | None = None
    target: str = ""
    target_path: str = ""
    confirmed: bool = False


class RunRestoreRequest(BaseModel):
    """Restore a run checkpoint by explicit id or latest checkpoint for a file."""
    checkpoint_id: str = ""
    target_path: str = ""
    confirmed: bool = False


class RecoveryActionResponse(BaseModel):
    """恢复动作执行结果"""
    ok: bool
    action_id: str
    status: str
    message: str
    event: dict[str, Any] | None = None


class RemediationRunRequest(BaseModel):
    """补救 run 请求"""
    failure_id: str = ""
    instruction: str = ""


# ============================================================
# 工作区注册、设置与健康
# ============================================================


class WorkspaceIdentity(BaseModel):
    """工作区身份"""
    workspace_id: str
    name: str
    path: str
    trusted: bool = False
    created_at: str = ""
    last_opened_at: str = ""
    nanocursor_version: str = ""


class WorkspaceSettings(BaseModel):
    """工作区设置"""
    model: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    indexing: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)


class WorkspaceHealth(BaseModel):
    """工作区健康状态"""
    workspace_id: str = ""
    path: str
    exists: bool = True
    writable: bool = True
    is_git_repo: bool = False
    index_status: str = "pending"
    setting_count: int = 0
    run_count: int = 0
    backup_count: int = 0


class OpenWorkspaceRequest(BaseModel):
    """打开工作区请求"""
    path: str = Field(..., min_length=1)


class SetWorkspaceRequest(BaseModel):
    """切换当前工作区请求（兼容旧前端的 dir 字段）"""
    dir: str = Field(..., min_length=1)


class McpServerUpsertRequest(BaseModel):
    """MCP Server 配置写入请求"""
    server_id: str = Field(..., min_length=1, max_length=80)
    command: str = Field(..., min_length=1, max_length=200)
    args: list[str] = Field(default_factory=list, max_length=50)
    env_keys: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    ignored_env_keys: list[str] = Field(default_factory=list, max_length=50)


class McpPresetInstallRequest(BaseModel):
    """内置 MCP 预设安装请求"""
    enabled: bool | None = None


class GitCommitRequest(BaseModel):
    """Git 提交请求"""
    message: str = Field(..., min_length=1, max_length=200)


class ContextPackRequest(BaseModel):
    """上下文打包请求"""
    objective: str = Field(default="", max_length=2000)
    workspace_dir: str | None = None


class EvalSuiteRunRequest(BaseModel):
    """Eval 套件运行请求"""
    eval_ids: list[str] = Field(default_factory=list)
    mode: str = "agent"
    stop_on_failure: bool = False


class IntentEvalRunRequest(BaseModel):
    """Intent routing eval suite request."""
    case_ids: list[str] = Field(default_factory=list)
    persist: bool = True


class RoutingEvalRunRequest(BaseModel):
    """Routing Decision eval suite request."""
    case_ids: list[str] = Field(default_factory=list)
    persist: bool = True


class AgentEvalRunRequest(BaseModel):
    """Aggregate agent-runtime eval suite request."""
    suite: Literal["core"] = "core"
    task_eval_ids: list[str] = Field(default_factory=list)
    persist: bool = True


class EvalScoreRequest(BaseModel):
    """Eval 重新评分请求"""
    signals: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSettingsUpdateRequest(BaseModel):
    """工作区设置更新请求（支持任意设置字段的部分更新）"""
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_body(cls, data: Any) -> Any:
        if isinstance(data, dict) and "settings" not in data:
            return {"settings": data}
        return data


class WorkspaceSettingsValidateRequest(BaseModel):
    """工作区设置校验请求。为空时校验当前持久化设置。"""
    settings: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_body(cls, data: Any) -> Any:
        if isinstance(data, dict) and "settings" not in data:
            return {"settings": data}
        return data


class ToolApprovalResolveRequest(BaseModel):
    """工具审批决策请求"""
    approved: bool = False
    comment: str = ""


class PolicyDecisionRecordRequest(BaseModel):
    """策略决策记录请求"""
    decision: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_body(cls, data: Any) -> Any:
        if isinstance(data, dict) and "decision" not in data:
            return {"decision": data}
        return data


class McpEnabledRequest(BaseModel):
    """MCP 服务启停请求"""
    enabled: bool = True


class McpToolCallRequest(BaseModel):
    """MCP 工具调用请求"""
    server_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    thread_id: str = ""
    approval_id: str = ""
    permission_level: str = ""
    timeout_seconds: int = 10

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_body(cls, data: Any) -> Any:
        known = {
            "server_id",
            "server",
            "tool_name",
            "tool",
            "arguments",
            "thread_id",
            "approval_id",
            "permission_level",
            "timeout_seconds",
        }
        if isinstance(data, dict) and "arguments" not in data and not (set(data) & known):
            return {"arguments": data}
        if isinstance(data, dict):
            normalized = dict(data)
            if "server" in normalized and "server_id" not in normalized:
                normalized["server_id"] = normalized.pop("server")
            if "tool" in normalized and "tool_name" not in normalized:
                normalized["tool_name"] = normalized.pop("tool")
            return normalized
        return data


class ConfirmedActionRequest(BaseModel):
    """需要确认的操作请求"""
    confirmed: bool = False


# ---------------------------------------------------------------------------
# R1: Delivery Contract
# ---------------------------------------------------------------------------


class DeliveryFinalizeRequest(BaseModel):
    """Finalize delivery contract for a run."""
    force: bool = False


class DeliveryRegenerateRequest(BaseModel):
    """Regenerate delivery contract from run data."""
    include_markdown: bool = True


# ---------------------------------------------------------------------------
# R2: Change Set
# ---------------------------------------------------------------------------


class ChangeSetCollectRequest(BaseModel):
    """Collect file changes for a run."""
    include_untracked: bool = True


class ChangeSetReviewRequest(BaseModel):
    """Review changes with risk rules."""
    mode: str = "rule_first"  # rule_first | llm_assist


class ChangeSetApproveRequest(BaseModel):
    """Approve or reject the change set."""
    approved: bool = True
    comment: str = ""


# ---------------------------------------------------------------------------
# R4: Failure Classification & Remediation
# ---------------------------------------------------------------------------


class RemediationRequest(BaseModel):
    """Request to remediate a failure."""
    mode: str = "auto"  # auto | manual
    confirmed: bool = True


# ---------------------------------------------------------------------------
# R5: Action Policy & Audit
# ---------------------------------------------------------------------------


class ActionCheckRequest(BaseModel):
    """Pre-flight check for an action."""
    kind: str           # ActionKind value
    target: str = ""    # file path, command, or tool name
    thread_id: str = ""
    payload: dict | None = None


class ActionExecuteRequest(BaseModel):
    """Execute an action through the unified pipeline."""
    kind: str
    target: str = ""
    payload: dict | None = None
    thread_id: str = ""
    approval_id: str = ""


# ---------------------------------------------------------------------------
# R6: Ephemeral Agents
# ---------------------------------------------------------------------------


class EphemeralAgentSuggestRequest(BaseModel):
    """Suggest temporary sub-agents for a run."""
    prompt: str = ""
    max_agents: int = 4
    mcp_plan: list[dict[str, Any]] = Field(default_factory=list)


class EphemeralAgentSpawnRequest(BaseModel):
    """Spawn one temporary sub-agent from a suggestion/spec."""
    agent: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_plain_spec(cls, data: Any) -> Any:
        if isinstance(data, dict) and "agent" not in data:
            return {"agent": data}
        return data


class EphemeralAgentCompleteRequest(BaseModel):
    """Complete a temporary sub-agent with structured output."""
    summary: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)


class EphemeralAgentArchiveRequest(BaseModel):
    """Archive a temporary sub-agent."""
    reason: str = ""
