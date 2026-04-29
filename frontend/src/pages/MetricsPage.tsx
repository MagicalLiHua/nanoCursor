/**
 * 指标面板页面
 *
 * 展示 LLM 调用指标、工具调用成功率、修复循环统计和近期 LLM 调用记录。
 * 所有数据从后端 /api/metrics 接口获取。
 */

import { useState, useEffect, useRef } from 'react';
import { useApp } from '../context/AppContext';
import type { MetricsData } from '../types';
import { getMetrics } from '../api/client';

/**
 * 单次 LLM 调用记录的类型
 */
interface LLMCallRecord {
  agent_name?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  latency_ms?: number;
}

/**
 * 指标卡片小组件
 * 展示单个指标的标签、值和副文本
 *
 * @param label 指标标签
 * @param value 指标值
 * @param sub 副文本（可选，通常描述趋势或详情）
 */
function MetricCard({ label, value, sub }: { label: string; value: string | number; sub: string }) {
  return (
    <div className="metric-card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

/**
 * 指标面板页面主组件
 */
export function MetricsPage() {
  // 指标数据状态
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  // 加载状态
  const [loading, setLoading] = useState(true);
  // 错误信息
  const [error, setError] = useState('');

  const { state } = useApp();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isRunningRef = useRef(false);
  isRunningRef.current = state.isRunning;

  /**
   * 加载指标数据
   * 从后端 API 获取并设置状态
   */
  async function loadMetrics() {
    try {
      setLoading(true);
      setError('');
      const data = await getMetrics();
      setMetrics(data);
    } catch (e: any) {
      setError(e.message || '获取指标失败');
    } finally {
      setLoading(false);
    }
  }

  // 初始加载（如果已经在运行则跳过，让轮询机制负责加载）
  useEffect(() => {
    if (!state.isRunning) {
      loadMetrics();
    }
  }, []);

  // 当工作流运行时，定时轮询指标数据
  useEffect(() => {
    if (state.isRunning) {
      if (!intervalRef.current) {
        // 立即获取一次最新数据，然后再开启轮询
        getMetrics()
          .then((data) => {
            setMetrics(data);
            setError('');
          })
          .catch((e: any) => {
            console.warn('Initial metrics fetch failed:', e);
          });

        intervalRef.current = setInterval(() => {
          if (isRunningRef.current) {
            getMetrics()
              .then((data) => {
                setMetrics(data);
                setError('');
              })
              .catch((e: any) => {
                console.warn('Polling metrics failed:', e);
              });
          }
        }, 1000);
      }
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      // 工作流停止后重新加载一次最终指标
      loadMetrics();
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [state.isRunning]);

  // 加载状态
  if (loading) {
    return (
      <>
        <div className="page-header">
          <h2>指标面板</h2>
          <p>查看 LLM 调用、工具使用、修复循环等指标</p>
        </div>
        <div className="page-content loading">
          <div className="spinner" />
          加载中...
        </div>
      </>
    );
  }

  // 错误状态
  if (error) {
    return (
      <>
        <div className="page-header">
          <h2>指标面板</h2>
          <p>查看 LLM 调用、工具使用、修复循环等指标</p>
        </div>
        <div className="page-content" style={{ textAlign: 'center', padding: '2rem' }}>
          <p style={{ color: '#ef4444' }}>{error}</p>
          <button className="primary" onClick={loadMetrics} style={{ marginTop: '1rem' }}>重新加载</button>
        </div>
      </>
    );
  }

  // 安全守卫：metrics 为 null 时显示空状态
  if (!metrics) {
    return (
      <>
        <div className="page-header">
          <h2>指标面板</h2>
          <p>查看 LLM 调用、工具使用、修复循环等指标</p>
        </div>
        <div className="page-content" style={{ textAlign: 'center', padding: '2rem' }}>
          <div className="empty-state" style={{ padding: '3rem' }}>
            <div className="placeholder-icon">~</div>
            <p>暂无指标数据，请先在工作台提交一次需求</p>
          </div>
        </div>
      </>
    );
  }

  // 提取指标值（处理可能为 undefined 或空对象的情况）
  const rawLlm = metrics.current?.llm;
  const llm: MetricsData['current']['llm'] = rawLlm && Object.keys(rawLlm).length > 0 ? rawLlm : {
    total_calls: 0, total_tokens: 0, avg_tokens_per_call: 0,
    avg_latency_ms: 0, max_latency_ms: 0, min_latency_ms: 0, total_latency_ms: 0,
  };
  const rawTools = metrics.current?.tool_calls;
  const tools: MetricsData['current']['tool_calls'] = rawTools && Object.keys(rawTools).length > 0 ? rawTools : {
    total: 0, successes: 0, failures: 0, success_rate: 0,
  };
  const rawRepair = metrics.current?.repair_cycles;
  const repair: MetricsData['current']['repair_cycles'] = rawRepair && Object.keys(rawRepair).length > 0 ? rawRepair : {
    total: 0, outcomes: [],
  };

  // 计算修复成功和失败数量
  const fixedCount = repair?.outcomes?.filter((o: any) => o.outcome === 'fixed').length ?? 0;
  const failedCount = repair?.outcomes?.filter((o: any) => o.outcome === 'still_failing').length ?? 0;

  return (
    <>
      <div className="page-header">
        <h2>指标面板</h2>
        <p>查看 LLM 调用、工具使用、修复循环等指标</p>
      </div>

      <div className="page-content">
        {/* 关键指标卡片 */}
        <div className="metrics-grid">
          <MetricCard
            label="LLM 总调用次数"
            value={llm.total_calls ?? 0}
            sub=""
          />
          <MetricCard
            label="总 Token 消耗"
            value={llm.total_tokens ?? 0}
            sub=""
          />
          <MetricCard
            label="平均每次 Token"
            value={Math.round(llm.avg_tokens_per_call ?? 0)}
            sub=""
          />
          <MetricCard
            label="平均耗时"
            value={`${Math.round(llm.avg_latency_ms ?? 0)} ms`}
            sub={`最快 ${Math.round(llm.min_latency_ms ?? 0)} ms`}
          />
          <MetricCard
            label="最大耗时"
            value={`${Math.round(llm.max_latency_ms ?? 0)} ms`}
            sub=""
          />
          <MetricCard
            label="工具成功率"
            value={`${Math.round((tools.success_rate ?? 0) * 100)}%`}
            sub={`${tools.successes ?? 0} / ${tools.total ?? 0} 次`}
          />
          <MetricCard
            label="修复循环数"
            value={repair.total ?? 0}
            sub={`成功 ${fixedCount} / 失败 ${failedCount}`}
          />
        </div>

        {/* 工具调用详情 */}
        <div className="config-section">
          <h3>工具调用详情</h3>
          <div className="config-row">
            <span className="config-key">总调用次数</span>
            <span className="config-val">{tools.total ?? 0}</span>
          </div>
          <div className="config-row">
            <span className="config-key">成功</span>
            <span className="config-val" style={{ color: '#16a34a' }}>{tools.successes ?? 0}</span>
          </div>
          <div className="config-row">
            <span className="config-key">失败</span>
            <span className="config-val" style={{ color: '#dc2626' }}>{tools.failures ?? 0}</span>
          </div>
          <div className="progress-bar-container" style={{ marginTop: '0.75rem' }}>
            <div className="progress-bar" style={{ width: `${(tools.success_rate ?? 0) * 100}%` }} />
          </div>
        </div>

        {/* 修复循环记录 */}
        {repair.outcomes && repair.outcomes.length > 0 && (
          <div className="config-section">
            <h3>修复循环记录</h3>
            <table className="data-table">
              <thead>
                <tr>
                  <th>次数</th>
                  <th>结果</th>
                  <th>错误摘要</th>
                </tr>
              </thead>
              <tbody>
                {repair.outcomes.map((o: any, i: number) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td style={{
                      fontWeight: 600,
                      color: o.outcome === 'fixed' ? '#16a34a' : '#dc2626',
                    }}>
                      {o.outcome === 'fixed' ? '已修复' : '仍失败'}
                    </td>
                    <td style={{ maxWidth: '400px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {o.error_summary ?? '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 空状态 */}
        {(!repair.outcomes || repair.outcomes.length === 0) && llm.total_calls === 0 && (
          <div className="empty-state" style={{ padding: '3rem' }}>
            <div className="placeholder-icon">~</div>
            <p>暂无指标数据，请先在工作台提交一次需求</p>
          </div>
        )}
      </div>
    </>
  );
}
