/**
 * 全局应用状态管理
 *
 * 使用 React Context 提供全局状态，避免层层 props 传递。
 * 状态包括：会话线程 ID、运行状态、聊天消息、执行日志等。
 */

import { createContext, useContext, useReducer, useEffect } from 'react';
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
  | { type: 'CLEAR_CHAT' }
  | { type: 'SET_THEME'; payload: 'light' | 'dark' }
  | { type: 'TOGGLE_THEME' }
  | { type: 'SET_WORKSPACE_DIR'; payload: string }
  | { type: 'SET_WORKSPACE_LIST'; payload: string[] };

/** 主题类型 */
export type Theme = 'light' | 'dark';

/** App 状态接口（含 theme） */
interface AppContextState extends AppState {
  theme: Theme;
  workspaceDir: string;
  workspaceList: string[];
}

/** 初始状态 */
const initialState: AppContextState = {
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
  theme: (localStorage.getItem('nanoCursor-theme') as Theme) || 'dark',
  workspaceDir: '',
  workspaceList: [],
};

/**
 * Reducer 函数，根据 action 类型更新状态
 */
function appReducer(state: AppContextState, action: AppAction): AppContextState {
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

    case 'SET_THEME':
      return { ...state, theme: action.payload };

    case 'TOGGLE_THEME':
      return { ...state, theme: state.theme === 'dark' ? 'light' : 'dark' };

    case 'SET_WORKSPACE_DIR':
      localStorage.setItem('nanoCursor-workspaceDir', action.payload);
      return { ...state, workspaceDir: action.payload };

    case 'SET_WORKSPACE_LIST':
      return { ...state, workspaceList: action.payload };

    default:
      return state;
  }
}

/** 创建 Context 对象，提供 state 和 dispatch（含 theme 便捷方法） */
const AppContext = createContext<{
  state: AppContextState;
  dispatch: React.Dispatch<AppAction>;
  theme: Theme;
  toggleTheme: () => void;
  setWorkspaceDir: (dir: string) => void;
  setWorkspaceList: (list: string[]) => void;
} | null>(null);

/**
 * 全局状态 Provider 组件
 */
export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  // 同步 theme 到 document root 和 localStorage
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', state.theme);
    localStorage.setItem('nanoCursor-theme', state.theme);
  }, [state.theme]);

  const toggleTheme = () => dispatch({ type: 'TOGGLE_THEME' });
  const setWorkspaceDir = (dir: string) => dispatch({ type: 'SET_WORKSPACE_DIR', payload: dir });
  const setWorkspaceList = (list: string[]) => dispatch({ type: 'SET_WORKSPACE_LIST', payload: list });

  return (
    <AppContext.Provider value={{ state, dispatch, theme: state.theme, toggleTheme, setWorkspaceDir, setWorkspaceList }}>
      {children}
    </AppContext.Provider>
  );
}

/**
 * 使用全局状态的 Hook
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