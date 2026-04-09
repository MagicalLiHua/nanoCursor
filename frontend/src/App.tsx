/**
 * 应用根组件
 *
 * 配置路由，渲染侧边栏和页面内容。
 * 使用 react-router-dom 实现页面间切换。
 */

import { BrowserRouter, Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { AppProvider, useApp } from './context/AppContext';
import './App.css';
import './index.css';
import { ChatPage } from './pages/ChatPage';
import { MetricsPage } from './pages/MetricsPage';
import { FileBrowserPage } from './pages/FileBrowserPage';
import { ConfigPage } from './pages/ConfigPage';

/**
 * Sidebar 组件
 *
 * 显示应用状态、当前计划、目标文件、重试信息、快速指标和导航链接。
 * 所有页面共享这一个侧边栏。
 */
function Sidebar() {
  // 获取全局状态
  const { state, dispatch } = useApp();

  /** 处理清空对话操作 */
  function handleClearChat() {
    dispatch({ type: 'CLEAR_CHAT' });
    // 同时生成新的 threadId，避免复用旧状态
    dispatch({ type: 'SET_THREAD_ID', payload: crypto.randomUUID() });
  }

  // 判断运行状态对应的 CSS 类
  const statusClass = state.isRunning ? 'running' : 'idle';
  const statusText = state.isRunning ? '运行中' : '空闲';

  // 重试进度条的百分比
  const retryPercent = state.retryInfo.max > 0
    ? (state.retryInfo.count / state.retryInfo.max) * 100
    : 0;

  // 获取当前路由，高亮对应导航项
  const location = useLocation();

  return (
    <aside className="sidebar">
      {/* 应用头部 */}
      <div className="sidebar-header">
        <h1>nanoCursor</h1>
        <p>基于 LangGraph + Docker 的多智能体自动编程框架</p>
      </div>

      {/* 会话信息 */}
      <div className="status-indicator">
        <span className={`status-dot ${statusClass}`} />
        <span>{statusText}</span>
      </div>
      <span className="thread-info">Thread: {state.threadId.slice(0, 12)}...</span>

      <button
        className="secondary"
        onClick={handleClearChat}
        disabled={state.isRunning}
        style={{ fontSize: '0.75rem' }}
      >
        清空对话
      </button>

      {/* 当前计划 */}
      {state.currentPlan && (
        <div className="sidebar-section">
          <div className="sidebar-section-title">当前计划</div>
          <div className="sidebar-section-content">
            <pre className="plan-text">{state.currentPlan}</pre>
          </div>
        </div>
      )}

      {/* 目标文件 */}
      {state.activeFiles.length > 0 && (
        <div className="sidebar-section">
          <div className="sidebar-section-title">目标文件</div>
          <div className="sidebar-section-content">
            <ul>
              {state.activeFiles.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* 重试进度 */}
      {state.retryInfo.count > 0 && (
        <div className="sidebar-section">
          <div className="sidebar-section-title">重试进度</div>
          <div className="sidebar-section-content">
            <div style={{ marginBottom: '0.25rem' }}>
              {state.retryInfo.count} / {state.retryInfo.max}
            </div>
            <div style={{
              width: '100%',
              height: '4px',
              backgroundColor: '#334155',
              borderRadius: '2px',
              overflow: 'hidden',
            }}>
              <div style={{
                width: `${retryPercent}%`,
                height: '100%',
                backgroundColor: retryPercent > 66 ? '#ef4444' : '#f59e0b',
                transition: 'width 0.3s',
              }} />
            </div>
          </div>
        </div>
      )}

      {/* 错误追踪 */}
      {state.errorTrace && (
        <div className="sidebar-section">
          <div className="sidebar-section-title">错误追踪</div>
          <div className="sidebar-section-content">
            <pre>{state.errorTrace.slice(0, 500)}</pre>
          </div>
        </div>
      )}

      {/* 快速指标 */}
      <div className="sidebar-section">
        <div className="sidebar-section-title">快速指标</div>
        <div className="quick-metrics">
          <div className="quick-metric">
            <span className="label">LLM 调用</span>
            <span className="value">{state.sidebarMetrics?.llm_calls ?? 0}</span>
          </div>
          <div className="quick-metric">
            <span className="label">总 Token</span>
            <span className="value">{state.sidebarMetrics?.total_tokens ?? 0}</span>
          </div>
          <div className="quick-metric">
            <span className="label">工具成功率</span>
            <span className="value">{Math.round(((state.sidebarMetrics?.tool_success_rate ?? 0) * 100))}%</span>
          </div>
        </div>
      </div>

      {/* 导航链接 */}
      <nav className="sidebar-nav">
        <NavLink
          to="/"
          className={({ isActive }) => isActive ? 'active' : ''}
        >
          工作台
        </NavLink>
        <NavLink
          to="/metrics"
          className={({ isActive }) => isActive ? 'active' : ''}
        >
          指标面板
        </NavLink>
        <NavLink
          to="/files"
          className={({ isActive }) => isActive ? 'active' : ''}
        >
          文件浏览器
        </NavLink>
        <NavLink
          to="/config"
          className={({ isActive }) => isActive ? 'active' : ''}
        >
          配置面板
        </NavLink>
      </nav>
    </aside>
  );
}

/**
 * 应用布局组件
 *
 * 包含固定的左侧边栏和右侧内容区。
 * Sidebar 固定显示，右侧根据路由切换页面。
 */
function AppLayout() {
  return (
    <div className="app-root">
      <Sidebar />
      <div className="main-content">
        <Routes>
          {/* 工作台（聊天页面） */}
          <Route path="/" element={<ChatPage />} />
          {/* 指标面板 */}
          <Route path="/metrics" element={<MetricsPage />} />
          {/* 文件浏览器 */}
          <Route path="/files" element={<FileBrowserPage />} />
          {/* 配置面板 */}
          <Route path="/config" element={<ConfigPage />} />
        </Routes>
      </div>
    </div>
  );
}

/**
 * 应用根组件
 *
 * 包裹 AppProvider 和 BrowserRouter，提供全局状态和路由。
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
