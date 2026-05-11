/**
 * 全局应用状态管理
 *
 * 精简版：只保留 threadId, isRunning, chatMessages, workspaceDir, sidebarMetrics, theme
 */

import { createContext, useContext, useReducer, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { AppState, ChatMessage, SidebarMetrics } from '../types';

type AppAction =
  | { type: 'SET_THREAD_ID'; payload: string }
  | { type: 'SET_RUNNING'; payload: boolean }
  | { type: 'ADD_CHAT_MESSAGE'; payload: ChatMessage }
  | { type: 'SET_CHAT_MESSAGES'; payload: ChatMessage[] }
  | { type: 'SET_SIDEBAR_METRICS'; payload: SidebarMetrics }
  | { type: 'CLEAR_CHAT' }
  | { type: 'SET_THEME'; payload: 'light' | 'dark' }
  | { type: 'TOGGLE_THEME' }
  | { type: 'SET_WORKSPACE_DIR'; payload: string }
  | { type: 'SET_WORKSPACE_LIST'; payload: string[] };

export type Theme = 'light' | 'dark';

interface AppContextState extends AppState {
  theme: Theme;
}

const initialState: AppContextState = {
  threadId: generateUUID(),
  isRunning: false,
  chatMessages: [],
  sidebarMetrics: null,
  theme: (localStorage.getItem('nanoCursor-theme') as Theme) || 'dark',
  workspaceDir: localStorage.getItem('nanoCursor-workspaceDir') || '',
  workspaceList: [],
};

function appReducer(state: AppContextState, action: AppAction): AppContextState {
  switch (action.type) {
    case 'SET_THREAD_ID':
      return { ...state, threadId: action.payload };

    case 'SET_RUNNING':
      return { ...state, isRunning: action.payload };

    case 'ADD_CHAT_MESSAGE':
      return { ...state, chatMessages: [...state.chatMessages, action.payload] };

    case 'SET_CHAT_MESSAGES':
      return { ...state, chatMessages: action.payload };

    case 'SET_SIDEBAR_METRICS':
      return { ...state, sidebarMetrics: action.payload };

    case 'CLEAR_CHAT':
      return {
        ...state,
        chatMessages: [],
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

const AppContext = createContext<{
  state: AppContextState;
  dispatch: React.Dispatch<AppAction>;
  theme: Theme;
  toggleTheme: () => void;
  setWorkspaceDir: (dir: string) => void;
  setWorkspaceList: (list: string[]) => void;
} | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

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

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within AppProvider');
  }
  return context;
}

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
