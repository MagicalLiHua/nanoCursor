/**
 * 应用根组件
 *
 * 配置路由，渲染侧边栏和页面内容。
 * 使用 react-router-dom 实现页面间切换。
 */

import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { AppProvider, useApp } from './context/AppContext';
import './App.css';
import './index.css';
import { ChatPage } from './pages/ChatPage';
import { MetricsPage } from './pages/MetricsPage';
import { FileBrowserPage } from './pages/FileBrowserPage';
import { ConfigPage } from './pages/ConfigPage';

/** SVG 图标组件 */
function IconChat() {
  return (
    <svg className="nav-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  );
}

function IconMetrics() {
  return (
    <svg className="nav-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
    </svg>
  );
}

function IconFiles() {
  return (
    <svg className="nav-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    </svg>
  );
}

function IconConfig() {
  return (
    <svg className="nav-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/>
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2"/>
    </svg>
  );
}

function IconSun() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5"/>
      <line x1="12" y1="1" x2="12" y2="3"/>
      <line x1="12" y1="21" x2="12" y2="23"/>
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
      <line x1="1" y1="12" x2="3" y2="12"/>
      <line x1="21" y1="12" x2="23" y2="12"/>
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
    </svg>
  );
}

function IconMoon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  );
}

function LogoMark() {
  return (
    <div className="sidebar-logo-mark">
      <svg viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="4" fill="white" opacity="0.9"/>
        <circle cx="12" cy="12" r="9" stroke="white" strokeWidth="1.5" opacity="0.5"/>
        <circle cx="12" cy="12" r="2" fill="white"/>
      </svg>
    </div>
  );
}

/**
 * Sidebar 组件
 */
function Sidebar() {
  const { state, dispatch, theme, toggleTheme } = useApp();

  function handleClearChat() {
    dispatch({ type: 'CLEAR_CHAT' });
    dispatch({ type: 'SET_THREAD_ID', payload: crypto.randomUUID() });
  }

  const statusClass = state.isRunning ? 'running' : 'idle';
  const statusText = state.isRunning ? '运行中' : '空闲';

  const retryPercent = state.retryInfo.max > 0
    ? (state.retryInfo.count / state.retryInfo.max) * 100
    : 0;

  return (
    <aside className="sidebar">
      {/* 应用头部 */}
      <div className="sidebar-header">
        <div className="sidebar-top-row">
          <div className="sidebar-logo">
            <LogoMark />
            <h1>nanoCursor</h1>
          </div>
          <button className="theme-toggle" onClick={toggleTheme} title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}>
            {theme === 'dark' ? <IconSun /> : <IconMoon />}
          </button>
        </div>
        <p>多智能体自动编程框架</p>
      </div>

      {/* 状态行 */}
      <div className="sidebar-status-row">
        <span className={`status-pill ${statusClass}`}>
          <span className="status-dot" />
          {statusText}
        </span>
        <span className="thread-chip" title={state.threadId}>
          {state.threadId.slice(0, 8)}
        </span>
      </div>

      <button className="sidebar-clear-btn" onClick={handleClearChat} disabled={state.isRunning}>
        清空对话
      </button>

      {/* 工作目录显示 */}
      {state.workspaceDir && (
        <div className="sidebar-workspace">
          <span className="sidebar-workspace-label">工作目录</span>
          <span className="sidebar-workspace-path" title={state.workspaceDir}>
            {state.workspaceDir}
          </span>
        </div>
      )}

      <div className="sidebar-body">
        {/* 当前计划 */}
        {state.currentPlan && (
          <div className="sidebar-section">
            <div className="sidebar-section-title">
              当前计划
              <span className="chevron">▼</span>
            </div>
            <div className="sidebar-section-content">
              <pre className="plan-text">{state.currentPlan}</pre>
            </div>
          </div>
        )}

        {/* 目标文件 */}
        {state.activeFiles.length > 0 && (
          <div className="sidebar-section">
            <div className="sidebar-section-title">
              目标文件
              <span className="chevron">▼</span>
            </div>
            <div className="sidebar-section-content">
              <ul>
                {state.activeFiles.map((f, i) => (
                  <li key={i}>
                    <span className="file-dot" />
                    {f.split('/').pop()}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* 重试进度 */}
        {state.retryInfo.count > 0 && (
          <div className="sidebar-section">
            <div className="sidebar-section-title">
              重试进度
              <span className="chevron">▼</span>
            </div>
            <div className="sidebar-section-content">
              <div className="retry-progress-row">
                <span>{state.retryInfo.count} / {state.retryInfo.max} 次</span>
                <span>{Math.round(retryPercent)}%</span>
              </div>
              <div className="retry-bar-track">
                <div className="retry-bar-fill" style={{ width: `${retryPercent}%` }} />
              </div>
            </div>
          </div>
        )}

        {/* 错误追踪 */}
        {state.errorTrace && (
          <div className="sidebar-section">
            <div className="sidebar-section-title">
              错误追踪
              <span className="chevron">▼</span>
            </div>
            <div className="sidebar-section-content">
              <pre>{state.errorTrace.slice(0, 500)}</pre>
            </div>
          </div>
        )}

        {/* 快速指标 */}
        <div className="sidebar-section">
          <div className="sidebar-section-title">
            实时指标
            <span className="chevron">▼</span>
          </div>
          <div className="sidebar-section-content">
            <div className="quick-metrics">
              <div className="quick-metric">
                <span className="label">LLM 调用</span>
                <span className="value">{state.sidebarMetrics?.llm_calls ?? 0}</span>
              </div>
              <div className="quick-metric">
                <span className="label">总 Token</span>
                <span className="value">{(state.sidebarMetrics?.total_tokens ?? 0).toLocaleString()}</span>
              </div>
              <div className="quick-metric">
                <span className="label">工具成功率</span>
                <span className="value">{Math.round((state.sidebarMetrics?.tool_success_rate ?? 0) * 100)}%</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 导航链接 */}
      <nav className="sidebar-nav">
        <NavLink to="/" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
          <IconChat />
          工作台
        </NavLink>
        <NavLink to="/metrics" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
          <IconMetrics />
          指标面板
        </NavLink>
        <NavLink to="/files" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
          <IconFiles />
          文件浏览器
        </NavLink>
        <NavLink to="/config" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
          <IconConfig />
          配置面板
        </NavLink>
      </nav>
    </aside>
  );
}

/**
 * 应用布局组件
 */
function AppLayout() {
  return (
    <div className="app-root">
      <Sidebar />
      <div className="main-content">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/metrics" element={<MetricsPage />} />
          <Route path="/files" element={<FileBrowserPage />} />
          <Route path="/config" element={<ConfigPage />} />
        </Routes>
      </div>
    </div>
  );
}

/**
 * 应用根组件
 */
function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <AppLayout />
      </BrowserRouter>
    </AppProvider>
  );
}

export default App;