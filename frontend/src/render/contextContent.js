import { capabilityKindLabel, escapeHtml, statusLabel } from "../core/format.js";
import { agentToneFromName, renderAgentAvatar } from "./chat.js";

let viewState = null;
let deps = {};

function setupContext(args) {
  viewState = args.state;
  deps = args;
}

export function renderTasks(args) {
  setupContext(args);
  return renderTasksView();
}

export function renderTeam(args) {
  setupContext(args);
  return renderTeamView();
}

export function renderEphemeralAgents(args) {
  setupContext(args);
  return renderEphemeralAgentsView();
}

function renderTasksView() {
  const visibleTasks = viewState.tasks.filter(isVisibleTask);
  const allCompleted =
    visibleTasks.length > 0 && visibleTasks.every((task) => ["completed", "skipped"].includes(task.status));
  const archiveCompleted = viewState.status === "completed" && allCompleted && !viewState.showCompletedTasks;

  if (archiveCompleted) {
    return `
      <div class="task-list">
        <section class="task-archive-summary">
          <div>
            <strong>${escapeHtml(visibleTasks.length)}</strong>
            <span>任务已完成并归档</span>
          </div>
          <button class="button secondary compact-button" data-action="toggle-completed-tasks" type="button">查看任务</button>
        </section>
      </div>
    `;
  }

  return `
    <div class="task-list">
      ${
        viewState.status === "completed" && allCompleted
          ? `<section class="task-archive-summary open">
              <div>
                <strong>${escapeHtml(visibleTasks.length)}</strong>
                <span>已展开完成任务</span>
              </div>
              <button class="button secondary compact-button" data-action="toggle-completed-tasks" type="button">收起任务</button>
            </section>`
          : ""
      }
      ${visibleTasks.length ? visibleTasks.map(renderTask).join("") : `<div class="empty-mini">任务生成中，等待 Planner 补齐标题和验收点</div>`}
    </div>
  `;
}

function renderTask(task) {
  const capabilities = task.capabilities || deps.inferTaskCapabilities(task);
  const evidence = Array.isArray(task.toolEvidence) ? task.toolEvidence : [];
  return `
    <article class="task-card ${escapeHtml(task.status || "idle")}">
      <div class="task-top">
        <div class="task-title">${escapeHtml(task.title)}</div>
        <span class="badge ${escapeHtml(task.status)}">${statusLabel(task.status)}</span>
      </div>
      <p class="task-desc">${escapeHtml(task.description)}</p>
      <div class="task-meta-row">
        <span class="panel-subtitle">负责人：${escapeHtml(task.owner)}</span>
        ${
          task.failure
            ? `<span class="task-failure">失败原因：${escapeHtml(task.failure)}</span>`
            : ""
        }
        ${
          capabilities.length
            ? `<div class="task-capabilities">
                ${capabilities
                  .slice(0, 4)
                  .map((capabilityId) => `<span>${escapeHtml(deps.capabilityDisplayName(capabilityId))}</span>`)
                  .join("")}
              </div>`
            : ""
        }
        ${
          evidence.length
            ? `<div class="task-evidence">
                ${evidence
                  .slice(-4)
                  .map(
                    (item) => `
                      <span title="${escapeHtml(item.capabilityId || item.capability_id || "")}">
                        ${escapeHtml(item.tool || "tool")}
                      </span>
                    `,
                  )
                  .join("")}
              </div>`
            : ""
        }
      </div>
    </article>
  `;
}

function isVisibleTask(task) {
  return Boolean(String(task?.title || "").trim() || String(task?.description || "").trim());
}

function renderTeamView() {
  return `
    <div class="team-list">
      <section class="conversation-team-banner">
        <div>
          <span>会话团队</span>
          <strong>${escapeHtml(viewState.currentConversationId || "尚未绑定会话")}</strong>
        </div>
        <button class="button secondary compact-button" data-action="refresh-conversation-team" type="button" ${viewState.currentConversationId ? "" : "disabled"}>重新推荐</button>
      </section>
      ${renderAgentCreateForm()}
      ${viewState.team.map((member, index) => renderTeamMember(member, index)).join("")}
    </div>
  `;
}

function renderAgentCreateForm() {
  const capabilityOptions = deps.getCapabilityOptions();
  return `
    <form class="agent-create" id="agent-create-form">
      <div class="agent-create-row">
        <input id="agent-name" placeholder="Agent 名称" maxlength="40" />
        <input id="agent-role" placeholder="角色，如 reviewer" maxlength="40" />
      </div>
      <textarea id="agent-goal" rows="2" placeholder="这个 Agent 负责什么？"></textarea>
      <div class="agent-capability-picker">
        <div class="agent-create-label">
          <span>能力包</span>
          <strong>${escapeHtml(capabilityOptions.length)} 项</strong>
        </div>
        <div class="capability-choice-list">
          ${capabilityOptions
            .map(
              (item) => `
                <label class="capability-choice">
                  <input type="checkbox" name="agent-capability" value="${escapeHtml(item.id)}" />
                  <span>${escapeHtml(item.name)}</span>
                  <small>${escapeHtml(capabilityKindLabel(item.kind))}</small>
                </label>
              `,
            )
            .join("")}
        </div>
      </div>
      <div class="agent-create-row">
        <input id="agent-tools" placeholder="补充工具，用逗号分隔" />
        <button class="button secondary" type="submit">添加</button>
      </div>
    </form>
  `;
}

function renderTeamMember(member, index = 0) {
  return `
    <article class="team-member">
      ${renderAgentAvatar(member.name || member.role, member.tone, "agent-dot")}
      <div class="team-member-body">
        <div class="agent-name">${escapeHtml(member.name)}</div>
        <div class="agent-role">${escapeHtml(member.role)}</div>
        ${member.goal ? `<p class="agent-goal">${escapeHtml(member.goal)}</p>` : ""}
        <div class="agent-card-meta">
          ${(member.tools || []).slice(0, 4).map((tool) => `<span>${escapeHtml(tool)}</span>`).join("")}
        </div>
        ${
          member.capabilities?.length
            ? `<div class="agent-capability-meta">
                ${member.capabilities
                  .slice(0, 4)
                  .map((capabilityId) => `<span>${escapeHtml(deps.capabilityDisplayName(capabilityId))}</span>`)
                  .join("")}
              </div>`
            : ""
        }
        ${member.lastAction ? `<div class="agent-last">${escapeHtml(member.lastAction)}</div>` : ""}
      </div>
      <div class="agent-card-actions">
        <span class="badge ${escapeHtml(member.status)}">${statusLabel(member.status)}</span>
        <button class="icon-button subtle" data-action="remove-team-member" data-index="${escapeHtml(index)}" title="移除该 Agent" type="button" ${viewState.team.length <= 1 ? "disabled" : ""}>×</button>
      </div>
    </article>
  `;
}

function renderEphemeralAgentsView() {
  const panel = viewState.ephemeralAgents || deps.blankEphemeralAgents();
  const threadReady = deps.isEphemeralThreadReady();
  const agents = Array.isArray(panel.agents) ? panel.agents : [];
  const suggestions = Array.isArray(panel.suggestions) ? panel.suggestions : [];
  const maxActive = panel.limits?.max_active_agents ?? 3;
  const activeCount = panel.active_count ?? agents.filter((agent) => !["archived", "expired"].includes(agent.status)).length;
  return `
    <div class="ephemeral-panel">
      <section class="ephemeral-hero">
        <div>
          <span>任务级子 Agent</span>
          <strong>${escapeHtml(threadReady ? viewState.currentThreadId : "等待运行 Thread")}</strong>
          <p>主 Agent 可为本次任务临时拉起专门执行者，完成后自动归档，不污染会话团队。</p>
        </div>
        <div class="ephemeral-hero-metrics">
          <div><strong>${escapeHtml(activeCount)}</strong><span>活跃</span></div>
          <div><strong>${escapeHtml(panel.archived_count ?? 0)}</strong><span>归档</span></div>
        </div>
      </section>
      <section class="ephemeral-toolbar">
        <button class="button primary compact-button" data-action="suggest-ephemeral-agents" type="button" ${threadReady ? "" : "disabled"}>
          ${panel.status === "loading" ? "生成中" : "生成建议"}
        </button>
        <button class="button secondary compact-button" data-action="refresh-ephemeral-agents" type="button" ${threadReady ? "" : "disabled"}>刷新</button>
        <button class="button secondary compact-button" data-action="toggle-archived-ephemeral" type="button">
          ${panel.includeArchived ? "隐藏归档" : "显示归档"}
        </button>
      </section>
      ${panel.error ? `<div class="inline-error">${escapeHtml(panel.error)}</div>` : ""}
      ${
        !threadReady
          ? `<div class="empty-mini">启动一次运行后，这里会显示本轮任务的临时子 Agent 建议和生命周期。</div>`
          : ""
      }
      ${
        suggestions.length
          ? `<section class="ephemeral-section">
              <div class="ephemeral-section-head">
                <span>推荐加入</span>
                <strong>${escapeHtml(suggestions.length)} / ${escapeHtml(panel.limits?.max_suggested_agents ?? 5)}</strong>
              </div>
              ${suggestions.map((agent, index) => renderEphemeralSuggestion(agent, index, activeCount >= maxActive)).join("")}
            </section>`
          : threadReady
            ? `<div class="empty-mini">暂无临时 Agent 建议。可以点击“生成建议”，让 Lead 按当前任务重新判断。</div>`
            : ""
      }
      <section class="ephemeral-section">
        <div class="ephemeral-section-head">
          <span>${panel.includeArchived ? "全部临时 Agent" : "活跃临时 Agent"}</span>
          <strong>${escapeHtml(agents.length)}</strong>
        </div>
        ${
          agents.length
            ? agents.map(renderEphemeralAgentCard).join("")
            : `<div class="empty-mini">还没有生成临时子 Agent。</div>`
        }
      </section>
    </div>
  `;
}

function renderEphemeralSuggestion(agent, index, limitReached = false) {
  const blocked = Array.isArray(agent.blocked_capabilities) ? agent.blocked_capabilities : [];
  return `
    <article class="ephemeral-card suggestion">
      <div class="ephemeral-card-top">
        ${renderAgentAvatar(agent.name || agent.role, agentToneFromName(agent.role || agent.name), "agent-dot")}
        <div>
          <strong>${escapeHtml(agent.name || agent.role || "临时 Agent")}</strong>
          <span>${escapeHtml(agent.role || "worker")} · ${escapeHtml(riskLabel(agent.risk_level))}</span>
        </div>
        <button class="button compact-button" data-action="spawn-ephemeral-agent" data-index="${escapeHtml(index)}" type="button" ${limitReached ? "disabled" : ""}>加入</button>
      </div>
      <p>${escapeHtml(agent.goal || agent.reason || "处理本轮任务中的独立子问题。")}</p>
      ${agent.reason ? `<div class="agent-last">${escapeHtml(agent.reason)}</div>` : ""}
      ${renderEphemeralAgentMeta(agent)}
      ${blocked.length ? `<div class="ephemeral-warning">待配置能力：${blocked.map((item) => escapeHtml(deps.capabilityDisplayName(item))).join("、")}</div>` : ""}
    </article>
  `;
}

function renderEphemeralAgentCard(agent) {
  const archived = ["archived", "expired"].includes(agent.status);
  const result = agent.result || {};
  return `
    <article class="ephemeral-card ${escapeHtml(agent.status || "active")}">
      <div class="ephemeral-card-top">
        ${renderAgentAvatar(agent.name || agent.role, agentToneFromName(agent.role || agent.name), "agent-dot")}
        <div>
          <strong>${escapeHtml(agent.name || agent.role || "临时 Agent")}</strong>
          <span>${escapeHtml(agent.role || "worker")} · ${escapeHtml(statusLabel(agent.status))}</span>
        </div>
        <span class="badge ${escapeHtml(agent.status || "active")}">${escapeHtml(statusLabel(agent.terminal_status || agent.status))}</span>
      </div>
      <p>${escapeHtml(agent.goal || agent.reason || "处理本轮任务中的独立子问题。")}</p>
      ${renderEphemeralAgentMeta(agent)}
      ${result.summary ? `<div class="ephemeral-result">${escapeHtml(result.summary)}</div>` : ""}
      ${
        archived
          ? agent.archive_reason
            ? `<div class="agent-last">${escapeHtml(agent.archive_reason)}</div>`
            : ""
          : `<div class="ephemeral-actions">
              <button class="button primary compact-button" data-action="complete-ephemeral-agent" data-agent-id="${escapeHtml(agent.agent_id)}" type="button">完成</button>
              <button class="button secondary compact-button" data-action="archive-ephemeral-agent" data-agent-id="${escapeHtml(agent.agent_id)}" type="button">归档</button>
            </div>`
      }
    </article>
  `;
}

function renderEphemeralAgentMeta(agent) {
  const capabilities = Array.isArray(agent.capabilities) ? agent.capabilities : [];
  const mcpServers = Array.isArray(agent.mcp_servers) ? agent.mcp_servers : [];
  const scope = agent.task_scope || {};
  const include = Array.isArray(scope.include) ? scope.include : [];
  return `
    <div class="agent-capability-meta">
      ${capabilities.slice(0, 4).map((item) => `<span>${escapeHtml(deps.capabilityDisplayName(item))}</span>`).join("")}
      ${mcpServers.slice(0, 2).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
    </div>
    ${
      include.length
        ? `<div class="ephemeral-scope">${include.slice(0, 3).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`
        : ""
    }
  `;
}

function riskLabel(risk) {
  const labels = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
  };
  return labels[risk] || risk || "中风险";
}
