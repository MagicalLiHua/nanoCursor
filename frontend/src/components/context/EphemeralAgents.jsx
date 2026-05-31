import React from "react";
import { statusLabel } from "../../core/format.js";

const RISK_LABELS = { low: "低风险", medium: "中风险", high: "高风险" };

function AgentAvatar({ name, role }) {
  const initial = (name || role || "?")[0]?.toUpperCase() || "?";
  return <span className="agent-dot">{initial}</span>;
}

function EphemeralAgentCard({ agent, onComplete, onArchive, capabilityDisplayName }) {
  const archived = ["archived", "expired"].includes(agent.status);
  const result = agent.result || {};
  const evidenceCount = Array.isArray(result.evidence) ? result.evidence.length : agent.evidence_count || 0;
  const riskCount = Array.isArray(result.risks) ? result.risks.length : agent.risk_count || 0;
  const actionCount = Array.isArray(result.recommended_next_actions) ? result.recommended_next_actions.length : 0;

  return (
    <article className={`ephemeral-card ${agent.status || "active"}`}>
      <div className="ephemeral-card-top">
        <AgentAvatar name={agent.name || agent.role} role={agent.role} />
        <div>
          <strong>{agent.name || agent.role || "临时 Agent"}</strong>
          <span>{agent.role || "worker"} · {statusLabel(agent.status)}</span>
        </div>
        <span className={`badge ${agent.status || "active"}`}>{statusLabel(agent.terminal_status || agent.status)}</span>
      </div>
      <p>{agent.goal || agent.reason || "处理本轮任务中的独立子问题。"}</p>
      <div className="agent-capability-meta">
        {(agent.tools || []).slice(0, 3).map((t, i) => <span key={i}>{t}</span>)}
        {(agent.capabilities || []).slice(0, 4).map((c, i) => <span key={i}>{capabilityDisplayName?.(c) || c}</span>)}
        {(agent.mcp_servers || []).slice(0, 2).map((s, i) => <span key={i}>{s}</span>)}
      </div>
      {result.summary && <div className="ephemeral-result">{result.summary}</div>}
      {(result.summary || evidenceCount || riskCount || actionCount) && (
        <div className="ephemeral-result-meta">
          <span>证据 {evidenceCount}</span>
          <span>风险 {riskCount}</span>
          <span>建议 {actionCount}</span>
        </div>
      )}
      {Array.isArray(result.recommended_next_actions) && result.recommended_next_actions.length > 0 && (
        <ul className="ephemeral-next-actions">
          {result.recommended_next_actions.slice(0, 3).map((item, i) => <li key={i}>{item}</li>)}
        </ul>
      )}
      {archived ? (
        agent.archive_reason && <div className="agent-last">{agent.archive_reason}</div>
      ) : (
        <div className="ephemeral-actions">
          <button className="button primary compact-button" onClick={() => onComplete?.(agent.agent_id)} type="button">完成</button>
          <button className="button secondary compact-button" onClick={() => onArchive?.(agent.agent_id)} type="button">归档</button>
        </div>
      )}
    </article>
  );
}

export default function EphemeralAgents({
  state, blankEphemeralAgents, isEphemeralThreadReady, capabilityDisplayName,
  onSuggest, onRefresh, onToggleArchived, onComplete, onArchive, onSpawn,
}) {
  const panel = state.ephemeralAgents || blankEphemeralAgents?.() || {};
  const threadReady = isEphemeralThreadReady?.() ?? true;
  const agents = Array.isArray(panel.agents) ? panel.agents : [];
  const suggestions = Array.isArray(panel.suggestions) ? panel.suggestions : [];
  const maxActive = panel.limits?.max_active_agents ?? 3;
  const activeCount = panel.active_count ?? agents.filter((a) => !["archived", "expired"].includes(a.status)).length;

  return (
    <div className="ephemeral-panel">
      <section className="ephemeral-hero">
        <div>
          <span>任务级子 Agent</span>
          <strong>{threadReady ? state.currentThreadId : "等待运行 Thread"}</strong>
          <p>主 Agent 可为本次任务临时拉起专门执行者，完成后自动归档，不污染会话团队。</p>
        </div>
        <div className="ephemeral-hero-metrics">
          <div><strong>{activeCount}</strong><span>活跃</span></div>
          <div><strong>{panel.archived_count ?? 0}</strong><span>归档</span></div>
        </div>
      </section>

      <section className="ephemeral-toolbar">
        <button className="button primary compact-button" onClick={onSuggest} type="button" disabled={!threadReady}>
          {panel.status === "loading" ? "生成中" : "生成建议"}
        </button>
        <button className="button secondary compact-button" onClick={onRefresh} type="button" disabled={!threadReady}>刷新</button>
        <button className="button secondary compact-button" onClick={onToggleArchived} type="button">
          {panel.includeArchived ? "隐藏归档" : "显示归档"}
        </button>
      </section>

      {panel.error && <div className="inline-error">{panel.error}</div>}
      {!threadReady && <div className="empty-mini">启动一次运行后，这里会显示本轮任务的临时子 Agent 建议和生命周期。</div>}

      {suggestions.length > 0 && (
        <section className="ephemeral-section">
          <div className="ephemeral-section-head">
            <span>推荐加入</span>
            <strong>{suggestions.length} / {panel.limits?.max_suggested_agents ?? 5}</strong>
          </div>
          {suggestions.map((agent, i) => (
            <article key={i} className="ephemeral-card suggestion">
              <div className="ephemeral-card-top">
                <AgentAvatar name={agent.name || agent.role} role={agent.role} />
                <div>
                  <strong>{agent.name || agent.role || "临时 Agent"}</strong>
                  <span>{agent.role || "worker"} · {RISK_LABELS[agent.risk_level] || agent.risk_level || "中风险"}</span>
                </div>
                <button className="button compact-button" onClick={() => onSpawn?.(i)} type="button" disabled={activeCount >= maxActive}>加入</button>
              </div>
              <p>{agent.goal || agent.reason || "处理本轮任务中的独立子问题。"}</p>
              {agent.reason && <div className="agent-last">{agent.reason}</div>}
            </article>
          ))}
        </section>
      )}

      {threadReady && !suggestions.length && (
        <div className="empty-mini">暂无临时 Agent 建议。可以点击"生成建议"，让 Lead 按当前任务重新判断。</div>
      )}

      <section className="ephemeral-section">
        <div className="ephemeral-section-head">
          <span>{panel.includeArchived ? "全部临时 Agent" : "活跃临时 Agent"}</span>
          <strong>{agents.length}</strong>
        </div>
        {agents.length ? (
          agents.map((agent, i) => (
            <EphemeralAgentCard key={agent.agent_id || i} agent={agent} onComplete={onComplete} onArchive={onArchive} capabilityDisplayName={capabilityDisplayName} />
          ))
        ) : panel.archived_count ? (
          <div className="empty-mini">本轮已有 {panel.archived_count} 个临时 Agent 归档。点击"显示归档"查看结果详情。</div>
        ) : (
          <div className="empty-mini">还没有生成临时子 Agent。</div>
        )}
      </section>
    </div>
  );
}
