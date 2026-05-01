/**
 * 工作台（聊天）页面
 *
 * 核心功能页面：用户输入需求，智能体规划、编码、测试的完整流程。
 * 通过 SSE (Server-Sent Events) 实时接收后端 LangGraph 工作流的执行事件，
 * 并流式展示到界面上。
 *
 * Gemini 风格：初始居中显示欢迎页（带聊天输入框），
 * 用户选择工作目录并输入需求后，点击发送进入完整聊天界面。
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import type { StreamEvent, LogEntry, MetricsData } from '../types';
import { startRun, getRunState, setWorkspace } from '../api/client';

/**
 * 工作流节点定义
 * 用于在工作流图中展示节点状态（已执行/待执行）
 */
const WORKFLOW_NODES = [
  { id: 'planner', label: 'Planner' },
  { id: 'planner_tools', label: 'Planner Tools' },
  { id: 'coder', label: 'Coder' },
  { id: 'coder_step_counter', label: 'Step Counter' },
  { id: 'coder_tools', label: 'Coder Tools' },
  { id: 'sandbox', label: 'Sandbox' },
  { id: 'reviewer', label: 'Reviewer' },
];

/**
 * ChatInput 组件
 *
 * 聊天输入框，包含输入字段和提交按钮。
 * 工作流运行时禁用输入。
 *
 * @param onSubmit 用户提交时调用，传入提示文本
 * @param disabled 是否禁用输入
 */
function ChatInput({ onSubmit, disabled }: { onSubmit: (prompt: string) => void; disabled: boolean }) {
  const [prompt, setPrompt] = useState('');

  /** 处理表单提交 */
  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setPrompt('');
  }

  return (
    <form className="chat-input-form" onSubmit={handleSubmit}>
      <input
        type="text"
        placeholder={disabled ? '工作流运行中...' : '输入你的需求，例如：用 Python 写一个快排'}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        disabled={disabled}
      />
      <button className="primary" type="submit" disabled={disabled || !prompt.trim()}>
        发送
      </button>
    </form>
  );
}

/**
 * WorkflowDiagram 组件
 *
 * 展示工作流图的节点状态。
 * 根据执行日志中已执行的节点，高亮已完成的节点。
 *
 * @param executedNodes 已执行的节点名称集合
 */
function WorkflowDiagram({ executedNodes }: { executedNodes: Set<string> }) {
  return (
    <div className="workflow-panel">
      <h3>工作流图</h3>
      {WORKFLOW_NODES.map((node) => {
        const isExecuted = executedNodes.has(node.id.toLowerCase());
        return (
          <div key={node.id} className={`workflow-node-item ${isExecuted ? 'executed' : 'pending'}`}>
            <div className="workflow-node-dot" />
            <span>{node.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * ChatPage 主页面
 *
 * 包含聊天消息区域、输入区域、执行轨迹面板和工作流图面板。
 * SSE 事件驱动状态更新。
 */
export function ChatPage() {
  const { state, dispatch, setWorkspaceDir } = useApp();
  // 用于自动滚动到最新消息
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // 标记是否正在获取最终状态，防止重复请求
  const hasFetchedFinalState = useRef(false);
  // 保存当前 SSE 连接，用于组件卸载时清理
  const eventSourceRef = useRef<EventSource | null>(null);
  // 防止双击提交
  const isSubmittingRef = useRef(false);
  // 避免 stale closure 的 running 状态 ref
  const isRunningRef = useRef(false);
  // 用于取消 finishRunAndFetchState
  const finishedRef = useRef(false);
  // 追踪最后一个 coder 消息的索引，用于更新而非创建新消息
  const lastCoderMsgIndexRef = useRef<number | null>(null);

  // 欢迎页状态
  const [workspacePath, setWorkspacePath] = useState('');
  const [welcomePrompt, setWelcomePrompt] = useState('');
  const [isEntering, setIsEntering] = useState(false);

  /** 每次添加新消息时滚动到底部 */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.chatMessages]);

  /** 组件卸载时关闭 SSE 连接 */
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, []);

  /**
   * 处理流程 SSE 事件
   *
   * 根据事件类型更新全局状态：添加日志、更新计划、设置错误、更新指标等。
   *
   * @param event SSE 事件对象
   */
  const processEvent = useCallback((event: StreamEvent) => {
    dispatch({ type: 'ADD_STREAM_EVENT', payload: event });

    if (event.type !== 'node_update' || !event.node) return;

    const node = event.node;
    const data = event.data as Record<string, unknown> | undefined;

    // 每个 node_update 附带当前指标，用于实时更新侧边栏
    if (data?.metrics) {
      const m = data.metrics as MetricsData['current'];
      dispatch({
        type: 'SET_SIDEBAR_METRICS',
        payload: {
          llm_calls: m.llm?.total_calls ?? 0,
          total_tokens: m.llm?.total_tokens ?? 0,
          tool_success_rate: m.tool_calls?.success_rate ?? 0,
        },
      });
    }

    // 根据节点类型，添加对应的执行日志
    switch (node) {
      case 'planner':
        dispatch({
          type: 'ADD_LOG_ENTRY',
          payload: { node: 'Planner', status: 'completed', detail: '生成了开发计划' },
        });
        if (data?.current_plan) {
          dispatch({ type: 'SET_CURRENT_PLAN', payload: data.current_plan as string });
        }
        break;

      case 'planner_tools':
        dispatch({
          type: 'ADD_LOG_ENTRY',
          payload: { node: 'Planner Tools', status: 'info', detail: '读取文件/列出目录' },
        });
        break;

      case 'coder': {
        dispatch({
          type: 'ADD_LOG_ENTRY',
          payload: { node: 'Coder', status: 'completed', detail: '代码/工具调用' },
        });
        // 如果 Coder 有输出文本内容，更新到同一条助手消息中，避免消息泛滥
        if (data?.content) {
          const content = data.content as string;
          if (content.length > 10) {
            if (lastCoderMsgIndexRef.current !== null && state.chatMessages[lastCoderMsgIndexRef.current]) {
              // 更新已有 coder 消息，追加内容
              const existing = state.chatMessages[lastCoderMsgIndexRef.current];
              dispatch({
                type: 'UPDATE_CHAT_MESSAGE',
                payload: { index: lastCoderMsgIndexRef.current, content: existing.content + '\n' + content.slice(0, 500) },
              });
            } else {
              // 创建新的 coder 消息
              dispatch({
                type: 'ADD_CHAT_MESSAGE',
                payload: { role: 'assistant', content: content.slice(0, 500) },
              });
              lastCoderMsgIndexRef.current = state.chatMessages.length;
            }
          }
        }
        break;
      }

      case 'coder_step_counter':
        dispatch({
          type: 'ADD_LOG_ENTRY',
          payload: {
            node: 'Coder Step',
            status: 'info',
            detail: `步数: ${data?.coder_step_count ?? 0}`,
          },
        });
        break;

      case 'coder_tools':
        dispatch({
          type: 'ADD_LOG_ENTRY',
          payload: { node: 'Coder Tools', status: 'info', detail: '读写文件' },
        });
        break;

      case 'sandbox': {
        const errorTrace = (data?.error_trace ?? '') as string;
        const retryCount = (data?.retry_count ?? 0) as number;
        const maxRetries = (data?.max_retries ?? 3) as number;

        if (errorTrace) {
          // 沙盒测试失败
          dispatch({ type: 'SET_ERROR_TRACE', payload: errorTrace });
          dispatch({
            type: 'ADD_LOG_ENTRY',
            payload: { node: 'Sandbox', status: 'failed', detail: '测试未通过' },
          });
        } else {
          // 沙盒测试通过
          dispatch({ type: 'SET_ERROR_TRACE', payload: '' });
          dispatch({
            type: 'ADD_LOG_ENTRY',
            payload: { node: 'Sandbox', status: 'completed', detail: '测试通过' },
          });
        }

        dispatch({ type: 'SET_RETRY_INFO', payload: { count: retryCount, max: maxRetries } });
        break;
      }

      case 'reviewer': {
        const content = (data?.content ?? '') as string;
        dispatch({
          type: 'ADD_LOG_ENTRY',
          payload: { node: 'Review', status: 'warning', detail: '分析错误并给出修复建议' },
        });
        break;
      }
    }
  }, [dispatch, state.chatMessages]);

  /**
   * 处理发送消息
   *
   * 1. 将用户消息添加到聊天
   * 2. 调用后端 API 启动工作流
   * 3. 通过 EventSource 连接 SSE 端点，流式接收事件
   * 4. 工作流完成后获取最终状态
   */
  const handleSendPrompt = useCallback(async (prompt: string, workspaceDir?: string) => {
    if (isSubmittingRef.current) return;
    isSubmittingRef.current = true;

    // 添加用户消息到聊天
    dispatch({ type: 'ADD_CHAT_MESSAGE', payload: { role: 'user', content: prompt } });

    // 清空旧状态，避免上一次运行的残留数据干扰
    dispatch({ type: 'SET_EXECUTION_LOG', payload: [] });
    dispatch({ type: 'SET_ERROR_TRACE', payload: '' });
    dispatch({ type: 'SET_CURRENT_PLAN', payload: '' });
    dispatch({ type: 'SET_ACTIVE_FILES', payload: [] });
    dispatch({ type: 'SET_RETRY_INFO', payload: { count: 0, max: 3 } });
    dispatch({ type: 'SET_MODIFICATION_LOG', payload: [] });
    dispatch({ type: 'SET_SIDEBAR_METRICS', payload: { llm_calls: 0, total_tokens: 0, tool_success_rate: 0 } });

    // 关闭上一次可能遗留的 SSE 连接
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    hasFetchedFinalState.current = false;

    // 设置运行状态
    isRunningRef.current = true;
    finishedRef.current = false;
    lastCoderMsgIndexRef.current = null;
    dispatch({ type: 'SET_RUNNING', payload: true });

    try {
      // 调用后端启动工作流
      const result = await startRun(prompt, state.threadId, workspaceDir || state.workspaceDir);

      // 连接 SSE 端点，流式接收事件
      const eventSource = new EventSource(`/api/run/${result.thread_id}/events`);
      eventSourceRef.current = eventSource;

      // 客户端超时：5 分钟无消息自动关闭
      const connectionTimeout = setTimeout(() => {
        if (eventSource.readyState === EventSource.OPEN) return;
        if (eventSource.readyState === EventSource.CONNECTING) {
          eventSource.close();
          dispatch({ type: 'SET_RUNNING', payload: false });
          dispatch({
            type: 'ADD_CHAT_MESSAGE',
            payload: { role: 'assistant', content: 'SSE 连接超时，请检查后端是否正常运行' },
          });
        }
      }, 300_000);

      eventSource.addEventListener('node_update', (e: Event) => {
        const msg = e as MessageEvent;
        try {
          const data = JSON.parse(msg.data) as StreamEvent;
          processEvent(data);
        } catch (err) {
          console.warn('Failed to parse node_update event:', msg.data, err);
          dispatch({
            type: 'ADD_CHAT_MESSAGE',
            payload: { role: 'assistant', content: `[数据解析错误] ${msg.data.slice(0, 100)}` },
          });
        }
      });

      eventSource.addEventListener('done', () => {
        clearTimeout(connectionTimeout);
        eventSource.close();
        if (eventSourceRef.current === eventSource) {
          eventSourceRef.current = null;
        }
        finishRunAndFetchState(result.thread_id);
      });

      // 处理所有错误（网络级 + 服务端 error 事件），统一用 onerror
      eventSource.onerror = () => {
        clearTimeout(connectionTimeout);
        eventSource.close();
        if (eventSourceRef.current === eventSource) {
          eventSourceRef.current = null;
        }
        if (isRunningRef.current) {
          isRunningRef.current = false;
          dispatch({ type: 'SET_RUNNING', payload: false });
          dispatch({
            type: 'ADD_CHAT_MESSAGE',
            payload: { role: 'assistant', content: 'SSE 连接中断，请检查后端状态' },
          });
        }
      };

    } catch (err: any) {
      dispatch({ type: 'SET_RUNNING', payload: false });
      dispatch({
        type: 'ADD_CHAT_MESSAGE',
        payload: { role: 'assistant', content: `启动工作流失败: ${err?.message ?? '未知错误'}` },
      });
    } finally {
      isSubmittingRef.current = false;
    }
  }, [dispatch, state.chatMessages]);

  /**
   * 工作流完成后获取最终状态
   *
   * 调用后端 /api/run/{threadId}/state 接口，
   * 更新计划、目标文件、修改记录等信息。
   * 同时添加一条总结性的助消息。
   *
   * @param threadId 线程 ID
   */
  async function finishRunAndFetchState(threadId: string) {
    if (hasFetchedFinalState.current) return;
    hasFetchedFinalState.current = true;
    finishedRef.current = true;

    try {
      // 短暂等待后端 checkpointer 完成持久化
      await new Promise(r => setTimeout(r, 500));

      // 重试 3 次获取最终状态
      let finalState: any = null;
      for (let i = 0; i < 3; i++) {
        try {
          finalState = await getRunState(threadId);
          if (finalState) break;
        } catch {
          // 第一次失败等待 1s 再重试
          if (i < 2) await new Promise(r => setTimeout(r, 1000));
        }
      }

      if (!finalState) throw new Error('无法获取最终状态');

      // 更新计划
      if (finalState.current_plan) {
        dispatch({ type: 'SET_CURRENT_PLAN', payload: finalState.current_plan });
      }

      // 更新目标文件列表
      if (finalState.active_files && Array.isArray(finalState.active_files)) {
        dispatch({ type: 'SET_ACTIVE_FILES', payload: finalState.active_files });
      }

      // 更新修改记录
      if (finalState.modification_log && Array.isArray(finalState.modification_log)) {
        dispatch({ type: 'SET_MODIFICATION_LOG', payload: finalState.modification_log });
      }

      // 添加一条完成消息
      const hasError = finalState.error_trace;
      const retryCount = finalState.retry_count ?? 0;

      if (hasError) {
        dispatch({
          type: 'SET_ERROR_TRACE',
          payload: finalState.error_trace,
        });
        dispatch({
          type: 'ADD_CHAT_MESSAGE',
          payload: {
            role: 'assistant',
            content: `工作流执行完成，但沙盒测试仍有错误（已重试 ${retryCount} 次）。请查看右侧错误追踪。`,
          },
        });
      } else {
        dispatch({
          type: 'ADD_CHAT_MESSAGE',
          payload: {
            role: 'assistant',
            content: '工作流执行完成，沙盒测试通过！',
          },
        });
      }
    } catch {
      // 获取最终状态失败时，仍然添加完成消息，但提示可能不完整
      dispatch({
        type: 'ADD_CHAT_MESSAGE',
        payload: { role: 'assistant', content: '工作流执行完成，但状态获取可能不完整' },
      });
    } finally {
      // 无论如何都重置运行状态
      isRunningRef.current = false;
      dispatch({ type: 'SET_RUNNING', payload: false });
    }
  }

  // 已执行节点的集合，用于工作流图高亮
  const executedNodes = new Set(state.executionLog.map(e => e.node));

  // 将修改记录映射为可读文本
  const modificationLogText = state.modificationLog.length > 0
    ? state.modificationLog.map((entry, i) => `${i + 1}. ${entry}`).join('\n')
    : '';

  // 未选择工作区时，显示欢迎页（带聊天输入框）
  if (!state.workspaceDir && !isEntering) {
    return (
      <div className="welcome-screen">
        {/* 背景装饰 */}
        <div className="welcome-bg-orb welcome-bg-orb-1" />
        <div className="welcome-bg-orb welcome-bg-orb-2" />
        <div className="welcome-bg-orb welcome-bg-orb-3" />

        <div className="welcome-content">
          {/* Logo */}
          <div className="welcome-logo-wrap">
            <div className="welcome-logo">
              <svg viewBox="0 0 24 24" fill="none" width="36" height="36">
                <circle cx="12" cy="12" r="4" fill="white" opacity="0.95"/>
                <circle cx="12" cy="12" r="9" stroke="white" strokeWidth="1.5" opacity="0.5"/>
                <circle cx="12" cy="12" r="2" fill="white"/>
              </svg>
            </div>
            <div className="welcome-logo-pulse" />
          </div>

          {/* 标题区块 */}
          <div className="welcome-header">
            <h1>nanoCursor</h1>
            <p>多智能体自动编程框架，让 AI 帮你写代码</p>
          </div>

          {/* 功能卡片 */}
          <div className="welcome-features">
            <div className="welcome-feature-card">
              <div className="feature-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>
                </svg>
              </div>
              <span>智能规划</span>
            </div>
            <div className="welcome-feature-card">
              <div className="feature-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <polyline points="16 18 22 12 16 6"/>
                  <polyline points="8 6 2 12 8 18"/>
                </svg>
              </div>
              <span>自动编码</span>
            </div>
            <div className="welcome-feature-card">
              <div className="feature-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
              </div>
              <span>沙盒测试</span>
            </div>
          </div>

          {/* 表单区块 */}
          <div className="welcome-form-wrap">
            <div className="welcome-form-label">设置工作目录</div>
            <input
              type="text"
              className="workspace-path-input"
              placeholder="输入工作目录路径，如 D:\projects\myapp"
              value={workspacePath}
              onChange={(e) => setWorkspacePath(e.target.value)}
            />

            <form
              className="welcome-chat-form"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!workspacePath.trim() || !welcomePrompt.trim()) return;
                try {
                  await setWorkspace(workspacePath.trim());
                } catch {
                  // 忽略错误
                }
                setWorkspaceDir(workspacePath.trim());
                setIsEntering(true);
                handleSendPrompt(welcomePrompt.trim(), workspacePath.trim());
              }}
            >
              <input
                type="text"
                className="welcome-chat-input"
                placeholder="输入你的需求，例如：用 Python 写一个快排"
                value={welcomePrompt}
                onChange={(e) => setWelcomePrompt(e.target.value)}
              />
              <button
                type="submit"
                className="welcome-send-btn"
                disabled={!workspacePath.trim() || !welcomePrompt.trim()}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"/>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            </form>
            <p className="workspace-hint">智能体将在指定目录下读写文件</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-header">
        <h2>工作台</h2>
        <p>输入你的需求，智能体会自动规划、编码、测试</p>
      </div>

      <div className="chat-layout">
        {/* 中间：聊天区域 */}
        <div className="chat-main">
          <div className="chat-messages">
            {/* 聊天消息列表 */}
            {state.chatMessages.map((msg, i) => (
              <div key={i} className={`chat-message ${msg.role}`}>
                {msg.content}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* 输入区域 */}
          <div className="chat-input-area">
            <ChatInput onSubmit={handleSendPrompt} disabled={state.isRunning} />
            {state.isRunning && (
              <div className="status-text">工作流运行中，请稍候...</div>
            )}
            {!state.isRunning && state.modificationLog.length > 0 && (
              <div className="status-text">
                修改记录：{modificationLogText.slice(0, 100)}
              </div>
            )}
          </div>
        </div>

        {/* 右侧：执行轨迹面板 */}
        <div className="execution-panel">
          <h3>执行轨迹</h3>
          {state.executionLog.length === 0 && (
            <div className="empty-state">
              <div className="placeholder-icon">~</div>
              <p>暂无执行记录</p>
            </div>
          )}
          {state.executionLog.map((entry, i) => (
            <div key={i} className="log-entry">
              <div className={`log-icon ${entry.status}`} />
              <div>
                <div className="log-node">{entry.node}</div>
                <div className="log-detail">{entry.detail}</div>
              </div>
            </div>
          ))}
        </div>

        {/* 最右：工作流图面板 */}
        <WorkflowDiagram executedNodes={executedNodes} />
      </div>
    </>
  );
}
