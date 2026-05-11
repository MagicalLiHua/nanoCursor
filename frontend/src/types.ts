/**
 * nanoCursor 前端类型定义
 */

/** 聊天消息对象 */
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** SSE 流式事件 */
export interface StreamEvent {
  type: string;
  node?: string;
  data?: Record<string, unknown>;
}

/** 侧边栏快速指标数据 */
export interface SidebarMetrics {
  llm_calls: number;
  total_tokens: number;
  tool_success_rate: number;
}

/** 应用全局状态（精简版） */
export interface AppState {
  threadId: string;
  isRunning: boolean;
  chatMessages: ChatMessage[];
  workspaceDir: string;
  workspaceList: string[];
  sidebarMetrics: SidebarMetrics | null;
}

/** 工作区文件信息 */
export interface FileInfo {
  path: string;
  isDir: boolean;
  size: number;
  mtime?: number;
}

/** 文件内容响应 */
export interface FileContent {
  content: string;
  size: number;
  lines: number;
  lang: string;
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
      successes: number;
      failures: number;
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
  id: string;
  timestamp: string;
  reason: string;
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

/** Todo 项 */
export interface TodoItem {
  id: string;
  title: string;
  status: 'pending' | 'completed' | 'cancelled';
  priority: number;
  category?: string;
  created_at: number;
  completed_at?: number;
}
