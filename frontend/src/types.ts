/**
 * 类型定义文件
 *
 * 定义整个前端应用使用的 TypeScript 类型和接口，
 * 与后端 API 响应格式和 AgentState 保持一致。
 */

/** 聊天气泡中的消息对象 */
export interface ChatMessage {
  // 消息角色，user 或 assistant
  role: 'user' | 'assistant';
  // 消息的文本内容
  content: string;
}

/** SSE 流式事件，来自后端的事件流 */
export interface StreamEvent {
  // 事件类型：node_update / done / error
  type: string;
  // 触发事件的节点名称，如 planner / coder / sandbox
  node?: string;
  // 节点传递的具体数据
  data?: Record<string, unknown>;
}

/** 执行日志中的单条记录 */
export interface LogEntry {
  // 节点名称，带图标前缀
  node: string;
  // 状态：completed / failed / warning / info
  status: string;
  // 简要描述
  detail: string;
}

/** 重试信息 */
export interface RetryInfo {
  // 当前重试次数
  count: number;
  // 最大重试次数
  max: number;
}

/** 侧边栏快速指标数据 */
export interface SidebarMetrics {
  llm_calls: number;
  total_tokens: number;
  tool_success_rate: number;
}

/** 应用全局状态，对应 AgentState 的前端映射 */
export interface AppState {
  // 会话线程 ID，用于 LangGraph checkpointer
  threadId: string;
  // 是否正在运行工作流
  isRunning: boolean;
  // SSE 流式事件列表
  streamEvents: StreamEvent[];
  // 当前的开发计划文本
  currentPlan: string;
  // 当前正在编辑的文件列表
  activeFiles: string[];
  // 重试信息
  retryInfo: RetryInfo;
  // 错误跟踪信息
  errorTrace: string;
  // 聊天消息列表
  chatMessages: ChatMessage[];
  // 执行轨迹日志
  executionLog: LogEntry[];
  // 修改记录日志
  modificationLog: string[];
  // 侧边栏快速指标（从 SSE 事件中实时推送）
  sidebarMetrics: SidebarMetrics | null;
}

/** 工作区文件信息 */
export interface FileInfo {
  // 相对路径
  path: string;
  // 是否为目录
  isDir: boolean;
  // 文件大小（字节）
  size: number;
  // 最后修改时间戳
  mtime?: number;
}

/** 文件内容响应 */
export interface FileContent {
  // 文件内容
  content: string;
  // 文件大小
  size: number;
  // 行数
  lines: number;
  // 语言标识符
  lang: string;
  // 修改时间
  mtime: number;
}

/** 指标数据 */
export interface MetricsData {
  current: {
    llm: {
      total_calls: number;
      total_tokens: number;
      avg_tokens_per_call: number;
      avg_latency_ms: number;
      max_latency_ms: number;
      min_latency_ms: number;
      total_latency_ms: number;
    };
    tool_calls: {
      total: number;
      success: number;
      failure: number;
      success_rate: number;
    };
    repair_cycles: {
      total: number;
      outcomes: Array<{ outcome: string; error_summary?: string }>;
    };
  };
  historical: unknown[];
}

/** 快照信息 */
export interface SnapshotInfo {
  // 快照 ID（目录名）
  id: string;
  // 创建时间
  timestamp: string;
  // 创建原因
  reason: string;
  // 活跃文件列表
  activeFiles: string[];
}

/** 快照详情 */
export interface SnapshotDetail {
  metadata: Record<string, unknown>;
  conversationSummary: string;
  codeFiles: Array<{ path: string; content: string }>;
}

/** 备份文件信息 */
export interface BackupInfo {
  name: string;
  size: number;
  mtime: number;
}

/** LLM 提供商配置状态 */
export interface LLMProvider {
  hasKey: boolean;
  model: string;
  baseUrl?: string;
}

/** 配置信息响应 */
export interface ConfigData {
  llmProviders: Record<string, LLMProvider>;
  system: Record<string, string | number>;
  envVars: Array<{ name: string; value: string; isSensitive: boolean; isSet: boolean }>;
}