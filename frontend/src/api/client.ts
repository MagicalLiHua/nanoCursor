/**
 * API 客户端模块
 *
 * 封装所有与 FastAPI 后端的 HTTP 通信，
 * 包括启动工作流、获取文件、指标、配置、快照等接口。
 */

import axios from 'axios';

/** 创建 axios 实例，所有请求以 /api 开头，由 Vite proxy 转发到后端 */
const api = axios.create({
  baseURL: '',
  timeout: 30000,
});

/**
 * 启动一个新的工作流运行
 *
 * @param prompt 用户输入的需求描述
 * @param threadId 可选的已有线程 ID，用于继续对话
 * @returns 包含 threadId 和状态的响应
 */
export async function startRun(prompt: string, threadId?: string) {
  const body: { prompt: string; thread_id?: string } = { prompt };
  if (threadId) {
    body.thread_id = threadId;
  }
  const { data } = await api.post<{ thread_id: string; status: string }>('/api/run', body);
  return data;
}

/**
 * 获取指定线程的最终状态
 *
 * @param threadId 线程 ID
 * @returns AgentState 状态字典
 */
export async function getRunState(threadId: string) {
  const { data } = await api.get(`/api/run/${threadId}/state`);
  return data;
}

/**
 * SSE 事件中附带的指标数据（与 MetricsData.current 结构相同）
 */
export interface MetricSnapshot {
  llm: {
    total_calls: number;
    total_tokens: number;
    avg_tokens_per_call: number;
    avg_latency_ms: number;
    max_latency_ms: number;
    min_latency_ms: number;
  };
  tool_calls: {
    total: number;
    success: number;
    failure: number;
    success_rate: number;
  };
  repair_cycles: {
    total: number;
    outcomes: Array<{ outcome: string; error?: string }>;
  };
  recent_llm_records: Array<{ node: string; tokens: number; latency_ms: number }>;
}

/**
 * 列出工作区中的所有文件和目录
 *
 * @returns 文件列表，每个包含 path、isDir、size、mtime
 */
export async function listFiles() {
  const { data } = await api.get<{ files: Array<{ path: string; is_dir: boolean; size: number; mtime?: number }> }>('/api/files');
  // 将后端 snake_case 字段转为 camelCase
  return {
    files: data.files.map(f => ({
      path: f.path,
      isDir: f.is_dir,
      size: f.size,
      mtime: f.mtime,
    })),
  };
}

/**
 * 读取指定文件的内容
 *
 * @param filePath 相对于工作区目录的路径
 * @returns 文件内容、大小、行数、语言类型等
 */
export async function readFileContent(filePath: string) {
  const { data } = await api.get<{ content: string; size: number; lines: number; lang: string; mtime: number }>(
    `/api/files/${encodeURIComponent(filePath)}`
  );
  return data;
}

/**
 * 获取指标数据
 *
 * @returns 当前指标和历史指标
 */
export async function getMetrics() {
  const { data } = await api.get('/api/metrics');
  return data;
}

/**
 * 获取配置信息
 *
 * @returns LLM 提供商状态、系统配置、环境变量
 */
export async function getConfig() {
  const { data } = await api.get('/api/config');
  // 将 snake_case 转为 camelCase
  const providers: Record<string, { hasKey: boolean; model: string; baseUrl?: string }> = {};
  for (const [key, val] of Object.entries(data.llm_providers as Record<string, { has_key: boolean; model: string; base_url?: string }>)) {
    const v = val as { has_key: boolean; model: string; base_url?: string };
    providers[key] = { hasKey: v.has_key, model: v.model, baseUrl: v.base_url };
  }
  return {
    llmProviders: providers,
    system: data.system as Record<string, string | number>,
    envVars: (data.env_vars as Array<{ name: string; value: string; is_sensitive: boolean; is_set: boolean }>).map(e => ({
      name: e.name,
      value: e.value,
      isSensitive: e.is_sensitive,
      isSet: e.is_set,
    })),
  };
}

/**
 * 列出所有恢复快照
 *
 * @returns 快照列表
 */
export async function listSnapshots() {
  const { data } = await api.get<{ snapshots: Array<{ id: string; timestamp: string; reason: string; active_files: string[] }> }>('/api/snapshots');
  return {
    snapshots: data.snapshots.map(s => ({
      id: s.id,
      timestamp: s.timestamp,
      reason: s.reason,
      activeFiles: s.active_files,
    })),
  };
}

/**
 * 获取指定快照的详细信息
 *
 * @param snapshotId 快照目录名称
 * @returns 快照元数据、对话摘要和代码文件
 */
export async function getSnapshot(snapshotId: string) {
  const { data } = await api.get(`/api/snapshots/${encodeURIComponent(snapshotId)}`);
  return {
    metadata: data.metadata,
    conversationSummary: data.conversation_summary,
    codeFiles: (data.code_files || []) as Array<{ path: string; content: string }>,
  };
}

/**
 * 列出所有备份文件
 *
 * @returns 备份文件列表
 */
export async function listBackups() {
  const { data } = await api.get<{ backups: Array<{ name: string; size: number; mtime: number }> }>('/api/backups');
  return { backups: data.backups };
}

/**
 * 读取指定备份文件的内容
 *
 * @param backupName 备份文件名
 * @returns 文件内容和元信息
 */
export async function readBackupContent(backupName: string) {
  const { data } = await api.get(`/api/backups/${encodeURIComponent(backupName)}`);
  return data;
}
