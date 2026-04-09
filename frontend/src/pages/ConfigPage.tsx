/**
 * 配置面板页面
 *
 * 展示三大板块：
 * 1. LLM 提供商配置：显示各提供商的 API Key、模型、地址等状态
 * 2. 系统配置：显示工作区路径、沙盒参数、上下文管理等
 * 3. 环境变量：列出所有环境变量（敏感信息脱敏）
 */

import { useState, useEffect } from 'react';
import { getConfig } from '../api/client';

/**
 * 配置面板页面主组件
 */
export function ConfigPage() {
  // 提供商配置状态
  const [llmProviders, setLlmProviders] = useState<Record<string, any>>({});
  // 系统配置状态
  const [systemConfig, setSystemConfig] = useState<Record<string, string | number>>({});
  // 环境变量列表
  const [envVars, setEnvVars] = useState<Array<{ name: string; value: string; isSensitive: boolean; isSet: boolean }>>([]);
  // 加载状态
  const [loading, setLoading] = useState(true);
  // 错误信息
  const [error, setError] = useState('');

  /**
   * 加载配置数据
   * 从后端 API 获取并设置状态值
   */
  async function loadConfig() {
    try {
      setLoading(true);
      setError('');
      const data = await getConfig();
      setLlmProviders(data.llmProviders);
      setSystemConfig(data.system as Record<string, string | number>);
      setEnvVars(data.envVars);
    } catch (e: any) {
      setError(e.message || '获取配置失败');
    } finally {
      setLoading(false);
    }
  }

  // 初始加载
  useEffect(() => {
    loadConfig();
  }, []);

  if (loading) {
    return (
      <>
        <div className="page-header">
          <h2>配置面板</h2>
          <p>查看和修改 LLM 提供商、系统参数和环境变量</p>
        </div>
        <div className="page-content loading">
          <div className="spinner" />
          加载中...
        </div>
      </>
    );
  }

  if (error) {
    return (
      <>
        <div className="page-header">
          <h2>配置面板</h2>
          <p>查看和修改 LLM 提供商、系统参数和环境变量</p>
        </div>
        <div className="page-content" style={{ textAlign: 'center', padding: '2rem' }}>
          <p style={{ color: '#ef4444' }}>{error}</p>
          <button className="primary" onClick={loadConfig} style={{ marginTop: '1rem' }}>重新加载</button>
        </div>
      </>
    );
  }

  // 提供商的显示名称映射
  const PROVIDER_NAMES: Record<string, string> = {
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    ollama: 'Ollama',
    deepseek: 'DeepSeek',
  };

  return (
    <>
      <div className="page-header">
        <h2>配置面板</h2>
        <p>查看 LLM 提供商、系统参数和环境变量</p>
      </div>

      <div className="page-content">
        {/* LLM 提供商配置 */}
        <div className="config-section">
          <h3>LLM 提供商配置</h3>
          <div className="provider-grid">
            {Object.entries(PROVIDER_NAMES).map(([key, name]) => {
              const provider = llmProviders[key];
              const isConnected = provider?.hasKey;

              return (
                <div key={key} className={`provider-card ${isConnected ? 'connected' : 'disconnected'}`}>
                  <div className="provider-name">{name}</div>
                  <div className="provider-detail">
                    模型: {provider?.model || '未配置'}
                  </div>
                  {provider?.baseUrl && (
                    <div className="provider-detail">
                      地址: {provider.baseUrl}
                    </div>
                  )}
                  <span className="status-badge">
                    {isConnected ? '已配置' : '未配置'}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* 系统配置 */}
        <div className="config-section">
          <h3>系统配置</h3>
          {Object.entries(systemConfig).map(([key, value]) => (
            <div className="config-row" key={key}>
              <span className="config-key">{key}</span>
              <span className="config-val">{String(value)}</span>
            </div>
          ))}
        </div>

        {/* 环境变量 */}
        <div className="config-section">
          <h3>环境变量</h3>
          {envVars.length === 0 ? (
            <div className="empty-state" style={{ padding: '2rem' }}>
              <div className="placeholder-icon">~</div>
              <p>暂无环境变量</p>
            </div>
          ) : (
            <table className="data-table" style={{ maxHeight: '400px', overflowY: 'auto', display: 'block' }}>
              <thead>
                <tr>
                  <th>变量名</th>
                  <th>值</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {envVars.map((env) => (
                  <tr key={env.name}>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{env.name}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      {env.isSensitive ? env.value : (env.value || '-')}
                    </td>
                    <td>
                      <span
                        className="status-badge"
                        style={{
                          backgroundColor: env.isSet ? '#dcfce7' : '#fef2f2',
                          color: env.isSet ? '#166534' : '#991b1b',
                          padding: '0.15rem 0.5rem',
                          borderRadius: '4px',
                          fontSize: '0.7rem',
                        }}
                      >
                        {env.isSet ? '已设置' : '未设置'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
