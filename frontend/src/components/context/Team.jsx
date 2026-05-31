import React from "react";
import { statusLabel } from "../../core/format.js";

function AgentAvatar({ name, tone }) {
  const initial = (name || "?")[0]?.toUpperCase() || "?";
  return <span className={`agent-dot tone-${tone || "default"}`}>{initial}</span>;
}

function TeamMember({ member, index, canRemove, onRemove }) {
  const tone = (member.name || "").toLowerCase().includes("lead") ? "lead" : (member.role || "default");
  return (
    <article className="team-member">
      <AgentAvatar name={member.name} tone={tone} />
      <div className="team-member-body">
        <div className="agent-name">{member.name}</div>
        <div className="agent-role">{member.role}</div>
        {member.goal && <p className="agent-goal">{member.goal}</p>}
        <div className="agent-card-meta">
          {(member.tools || []).slice(0, 4).map((tool, i) => <span key={i}>{tool}</span>)}
        </div>
        {member.lastAction && <div className="agent-last">{member.lastAction}</div>}
      </div>
      <div className="agent-card-actions">
        <span className={`badge ${member.status}`}>{statusLabel(member.status)}</span>
        <button className="icon-button subtle" onClick={() => onRemove?.(index)} title="移除该 Agent" type="button" disabled={!canRemove}>×</button>
      </div>
    </article>
  );
}

function EphemeralSuggestion({ agent, index, limitReached, onSpawn, capabilityDisplayName }) {
  const blocked = Array.isArray(agent.blocked_capabilities) ? agent.blocked_capabilities : [];
  const RISK_LABELS = { low: "低风险", medium: "中风险", high: "高风险" };
  return (
    <article className="ephemeral-card suggestion">
      <div className="ephemeral-card-top">
        <AgentAvatar name={agent.name || agent.role} tone={agent.role || "default"} />
        <div>
          <strong>{agent.name || agent.role || "临时 Agent"}</strong>
          <span>{agent.role || "worker"} · {RISK_LABELS[agent.risk_level] || agent.risk_level || "中风险"}</span>
        </div>
        <button className="button compact-button" onClick={() => onSpawn?.(index)} type="button" disabled={limitReached}>加入</button>
      </div>
      <p>{agent.goal || agent.reason || "处理本轮任务中的独立子问题。"}</p>
      {agent.reason && <div className="agent-last">{agent.reason}</div>}
      {blocked.length > 0 && (
        <div className="ephemeral-warning">
          待配置能力：{blocked.map((item) => capabilityDisplayName?.(item) || item).join("、")}
        </div>
      )}
    </article>
  );
}

export default function Team({
  state, capabilityDisplayName, blankEphemeralAgents, isEphemeralThreadReady,
  getCapabilityOptions, onRemoveMember, onSpawnEphemeral, onSuggestEphemeral, onRefreshEphemeral,
}) {
  const panel = state.ephemeralAgents || blankEphemeralAgents?.() || {};
  const agents = Array.isArray(panel.agents) ? panel.agents : [];
  const suggestions = Array.isArray(panel.suggestions) ? panel.suggestions : [];
  const maxActive = panel.limits?.max_active_agents ?? 3;
  const activeCount = panel.active_count ?? agents.filter((a) => !["archived", "expired"].includes(a.status)).length;
  const isDefaultTeam = state.team.length === 1 && String(state.team[0]?.role || "").toLowerCase() === "lead";
  const threadReady = isEphemeralThreadReady?.() ?? true;
  const running = ["running", "waiting_approval", "cancelling"].includes(state.status);
  const visibleAgents = running ? agents.filter((a) => !["archived", "expired"].includes(a.status)) : [];
  const visibleSuggestions = running ? suggestions : [];

  return (
    <div className="team-list">
      <section className="conversation-team-banner">
        <div>
          <span>会话团队</span>
          <strong>{state.currentConversationId || "尚未绑定会话"}</strong>
        </div>
        <button className="button secondary compact-button" data-action="refresh-conversation-team" type="button" disabled={!state.currentConversationId}>重新推荐</button>
      </section>

      {isDefaultTeam && (
        <div className="empty-card">
          <div className="empty-card-icon">👥</div>
          <strong>发送需求后自动组队</strong>
          <p>Lead 会根据任务类型自动招募 Planner、Coder、Tester 等 Agent。你也可以在下方手动添加自定义 Agent。</p>
        </div>
      )}

      <form className="agent-create" id="agent-create-form">
        <div className="agent-create-row">
          <input id="agent-name" placeholder="Agent 名称" maxLength="40" />
          <input id="agent-role" placeholder="角色，如 reviewer" maxLength="40" />
        </div>
        <textarea id="agent-goal" rows="2" placeholder="这个 Agent 负责什么？" />
        <div className="agent-capability-picker">
          <div className="agent-create-label">
            <span>能力包</span>
            <strong>{getCapabilityOptions?.()?.length || 0} 项</strong>
          </div>
          <div className="capability-choice-list">
            {(getCapabilityOptions?.() || []).map((item) => (
              <label key={item.id} className="capability-choice">
                <input type="checkbox" name="agent-capability" value={item.id} />
                <span>{item.name}</span>
              </label>
            ))}
          </div>
        </div>
        <div className="agent-create-row">
          <input id="agent-tools" placeholder="补充工具，用逗号分隔" />
          <button className="button secondary" type="submit">添加</button>
        </div>
      </form>

      {state.team.map((member, i) => (
        <TeamMember key={i} member={member} index={i} canRemove={state.team.length > 1} onRemove={onRemoveMember} />
      ))}

      <section className="ephemeral-section">
        <div className="ephemeral-section-head">
          <span>临时子 Agent</span>
          <strong>{running ? `${activeCount} 活跃` : "本轮结束后自动归档"}</strong>
        </div>
        <div className="ephemeral-inline">
          {running && (
            <div className="ephemeral-toolbar">
              <button className="button primary compact-button" onClick={onSuggestEphemeral} type="button" disabled={!threadReady}>
                {panel.status === "loading" ? "生成中" : "生成建议"}
              </button>
              <button className="button secondary compact-button" onClick={onRefreshEphemeral} type="button" disabled={!threadReady}>刷新</button>
            </div>
          )}
          {visibleSuggestions.map((agent, i) => (
            <EphemeralSuggestion key={i} agent={agent} index={i} limitReached={activeCount >= maxActive} onSpawn={onSpawnEphemeral} capabilityDisplayName={capabilityDisplayName} />
          ))}
          {visibleAgents.length ? (
            visibleAgents.slice(0, 5).map((agent, i) => (
              <article key={i} className={`ephemeral-card ${agent.status || "active"}`}>
                <div className="ephemeral-card-top">
                  <AgentAvatar name={agent.name || agent.role} tone={agent.role || "default"} />
                  <div>
                    <strong>{agent.name || agent.role || "临时 Agent"}</strong>
                    <span>{agent.role || "worker"} · {statusLabel(agent.status)}</span>
                  </div>
                </div>
                <p>{agent.goal || agent.reason || "处理本轮任务中的独立子问题。"}</p>
              </article>
            ))
          ) : running && threadReady ? (
            <div className="empty-mini">暂无临时 Agent</div>
          ) : (
            <div className="empty-mini">临时 Agent 只在当前消息执行期间展示，完成后会回收到事件和交付证据中。</div>
          )}
        </div>
      </section>
    </div>
  );
}
