/**
 * 全局应用状态管理
 *
 * 使用 React Context 提供全局状态，避免层层 props 传递。
 * 状态包括：会话线程 ID、运行状态、聊天消息、执行日志等。
 */

import { createContext, useContext, useReducer } from 'react';
import type { ReactNode } from 'react';
import type { AppState, ChatMessage, StreamEvent, LogEntry, RetryInfo, SidebarMetrics } from '../types';

/** 定义可用的 action 类型 */
type AppAction =
  | { type: 'SET_THREAD_ID'; payload: string }
  | { type: 'SET_RUNNING'; payload: boolean }
  | { type: 'ADD_STREAM_EVENT'; payload: StreamEvent }
  | { type: 'ADD_CHAT_MESSAGE'; payload: ChatMessage }
  | { type: 'UPDATE_CHAT_MESSAGE'; payload: { index: number; content: string } }
  | { type: 'SET_CHAT_MESSAGES'; payload: ChatMessage[] }
  | { type: 'SET_CURRENT_PLAN'; payload: string }
  | { type: 'SET_ACTIVE_FILES'; payload: string[] }
  | { type: 'SET_RETRY_INFO'; payload: RetryInfo }
  | { type: 'SET_ERROR_TRACE'; payload: string }
  | { type: 'ADD_LOG_ENTRY'; payload: LogEntry }
  | { type: 'SET_EXECUTION_LOG'; payload: LogEntry[] }
  | { type: 'SET_MODIFICATION_LOG'; payload: string[] }
  | { type: 'SET_SIDEBAR_METRICS'; payload: SidebarMetrics }
  | { type: 'CLEAR_CHAT' };

/** 初始状态 */
const initialState: AppState = {
  threadId: generateUUID(),
  isRunning: false,
  streamEvents: [],
  currentPlan: '',
  activeFiles: [],
  retryInfo: { count: 0, max: 3 },
  errorTrace: '',
  chatMessages: [],
  executionLog: [],
  modificationLog: [],
  sidebarMetrics: null,
};

/**
 * Reducer 函数，根据 action 类型更新状态
 *
 * @param state 当前状态
 * @param action 要执行的 action
 * @returns 新的状态
 */
function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'SET_THREAD_ID':
      return { ...state, threadId: action.payload };

    case 'SET_RUNNING':
      return { ...state, isRunning: action.payload };

    case 'ADD_STREAM_EVENT':
      return { ...state, streamEvents: [...state.streamEvents, action.payload] };

    case 'ADD_CHAT_MESSAGE':
      return { ...state, chatMessages: [...state.chatMessages, action.payload] };

    case 'UPDATE_CHAT_MESSAGE': {
      const messages = [...state.chatMessages];
      if (action.payload.index >= 0 && action.payload.index < messages.length) {
        messages[action.payload.index] = { ...messages[action.payload.index], content: action.payload.content };
      }
      return { ...state, chatMessages: messages };
    }

    case 'SET_CHAT_MESSAGES':
      return { ...state, chatMessages: action.payload };

    case 'SET_CURRENT_PLAN':
      return { ...state, currentPlan: action.payload };

    case 'SET_ACTIVE_FILES':
      return { ...state, activeFiles: action.payload };

    case 'SET_RETRY_INFO':
      return { ...state, retryInfo: action.payload };

    case 'SET_ERROR_TRACE':
      return { ...state, errorTrace: action.payload };

    case 'ADD_LOG_ENTRY':
      return { ...state, executionLog: [...state.executionLog, action.payload] };

    case 'SET_EXECUTION_LOG':
      return { ...state, executionLog: action.payload };

    case 'SET_MODIFICATION_LOG':
      return { ...state, modificationLog: action.payload };

    case 'SET_SIDEBAR_METRICS':
      return { ...state, sidebarMetrics: action.payload };

    case 'CLEAR_CHAT':
      return {
        ...state,
        chatMessages: [],
        streamEvents: [],
        currentPlan: '',
        activeFiles: [],
        retryInfo: { count: 0, max: 3 },
        errorTrace: '',
        executionLog: [],
        modificationLog: [],
        sidebarMetrics: null,
      };

    default:
      return state;
  }
}

/** 创建 Context 对象，提供 state 和 dispatch */
const AppContext = createContext<{
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
} | null>(null);

/**
 * 全局状态 Provider 组件
 *
 * 包裹在应用根组件上，让所有子组件都能访问全局状态。
 *
 * @param children 子组件
 */
export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

/**
 * 使用全局状态的 Hook
 *
 * 在任何组件中调用 useApp() 即可获取 state 和 dispatch。
 * 如果在 AppProvider 外部使用会抛出错误。
 */
export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp 必须在 AppProvider 内部使用');
  }
  return context;
}

/** 辅助函数：生成 UUID v4 */
function generateUUID(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
