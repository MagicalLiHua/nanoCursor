import { approvalDecisionLabel, escapeHtml, shortId } from "../core/format.js";

export function renderChat({ state, isActionBusy }) {
  const running = state.status === "running";
  const taskCount = state.tasks?.length || 0;
  const fileCount = state.files?.length || 0;
  const sessionLabel =
    state.currentThreadId && state.currentThreadId !== "pending"
      ? shortId(state.currentThreadId, "Draft")
      : state.currentConversationId
        ? shortId(state.currentConversationId, "Draft")
        : "Draft";
  return `
    <section class="panel chat-panel">
      <div class="panel-header chat-workbar">
        <div class="chat-title-block">
          <h2 class="panel-title">工作会话</h2>
          <span class="panel-subtitle">${escapeHtml(sessionLabel)}</span>
        </div>
        <div class="chat-workbar-meta">
          <span>${escapeHtml(taskCount)} tasks</span>
          <span>${escapeHtml(fileCount)} files</span>
          <span>${escapeHtml(state.team?.length || 0)} agents</span>
        </div>
      </div>
      <div class="chat-body">
        <div class="message-list" id="message-list">
          ${state.messages.map(renderMessage).join("")}
        </div>
        ${renderApprovalPanel(state)}
        ${renderCapabilityRecommendation(state)}
        <form class="prompt-box" id="prompt-form">
          <div class="composer-toolbar">
            <div class="composer-modes" aria-label="任务模式">
              <span class="composer-mode active">Agent</span>
              <span class="composer-mode">Edit</span>
              <span class="composer-mode">Review</span>
            </div>
            <span class="composer-hint">Cmd/Ctrl + Enter 运行</span>
          </div>
          <textarea class="prompt-input" id="prompt-input" rows="2" placeholder="描述你想让 nanoCursor 完成的代码任务" title="Cmd/Ctrl + Enter 运行">${escapeHtml(state.prompt)}</textarea>
          <button class="button ${isActionBusy("run-prompt") ? "loading" : ""}" type="submit" ${running || isActionBusy("run-prompt") ? "disabled" : ""}>${running ? "运行中" : isActionBusy("run-prompt") ? "连接中" : "发送"}</button>
        </form>
      </div>
    </section>
  `;
}

function renderCapabilityRecommendation(state) {
  if (state.capabilityRecommendationDismissed || state.capabilityRecommendationMuted) return "";
  if (!String(state.prompt || "").trim()) return "";

  const recommendation = state.capabilityRecommendation || {};
  const capabilities = recommendation.capabilities || [];
  const agents = recommendation.agents || [];
  if (!agents.length && !capabilities.length) return "";
  const expanded = Boolean(state.ui?.recommendationExpanded);

  return `
    <section class="recommend-panel ${expanded ? "expanded" : "compact"}">
      <div class="recommend-head">
        <div>
          <span>智能组队建议</span>
          <strong>${escapeHtml(agents.slice(0, 4).join(" / "))}</strong>
        </div>
        <div class="recommend-actions">
          <button class="button secondary compact-button" data-action="toggle-recommendation-detail" type="button">${expanded ? "收起" : "展开"}</button>
          <button class="button secondary compact-button" data-action="show-capabilities" type="button">能力</button>
          <button class="icon-button subtle" data-action="dismiss-recommendation" title="关闭智能组队建议" type="button">×</button>
        </div>
      </div>
      ${
        expanded
          ? `<div class="recommend-capabilities">
              ${capabilities
                .slice(0, 8)
                .map(
                  (item) => `
                    <span class="${escapeHtml(item.status || "ready")}">
                      ${escapeHtml(item.name || item.id)}
                    </span>
                  `,
                )
                .join("")}
            </div>`
          : ""
      }
      ${
        recommendation.reasons?.length && expanded
          ? `<p>${escapeHtml(recommendation.reasons[0])}</p>`
          : ""
      }
    </section>
  `;
}

function renderApprovalPanel(state) {
  const approval = state.approval || {};
  if (!approval.status || approval.status === "idle" || approval.status === "resolved") return "";

  const tasks = approval.tasks || [];
  const isPending = approval.status === "pending";
  const isToolApproval = approval.kind === "tool";
  return `
    <section class="approval-panel ${escapeHtml(approval.status)}">
      <div class="approval-head">
        <div>
          <span class="approval-kicker">${escapeHtml(isToolApproval ? "工具审批" : "计划审批")}</span>
          <h3>${escapeHtml(approval.title || "等待用户审批计划")}</h3>
        </div>
        <span class="badge ${isPending ? "warning" : approval.decision || "ready"}">
          ${isPending ? "待审批" : approvalDecisionLabel(approval.decision)}
        </span>
      </div>
      <p>${escapeHtml(approval.content || "")}</p>
      ${
        tasks.length
          ? `<div class="approval-tasks">
              ${tasks
                .slice(0, 4)
                .map(
                  (task, index) => `
                    <div class="approval-task">
                      <strong>${escapeHtml(index + 1)}</strong>
                      <span>${escapeHtml(task.title || task.id || task)}</span>
                    </div>
                  `,
                )
                .join("")}
            </div>`
          : ""
      }
      ${
        isPending
          ? `
            <textarea class="approval-comment" id="approval-comment" rows="2" placeholder="可选：给 Planner 留下审批意见"></textarea>
            <div class="approval-actions">
              <button class="button" data-action="approval-decision" data-decision="approved">批准</button>
              ${isToolApproval ? "" : `<button class="button secondary" data-action="approval-decision" data-decision="revise">修改</button>`}
              <button class="button secondary" data-action="approval-decision" data-decision="rejected">拒绝</button>
            </div>
          `
          : `<div class="approval-result">${escapeHtml(approval.comment || approvalDecisionLabel(approval.decision))}</div>`
      }
    </section>
  `;
}

function renderMessage(message) {
  const isUser = message.role === "user";
  const tone = isUser ? "user" : agentToneFromName(message.author);
  return `
    <article class="message ${isUser ? "user" : ""}">
      ${renderAgentAvatar(message.author, tone, "avatar")}
      <div class="bubble">
        <div class="message-head">
          <span class="message-author">${escapeHtml(message.author)}</span>
          <span>${escapeHtml(message.time)}</span>
        </div>
        <p class="message-text">${escapeHtml(message.content)}</p>
      </div>
    </article>
  `;
}

export function renderAgentAvatar(name, tone = "lead", extraClass = "") {
  const safeTone = agentToneFromName(name, tone);
  return `
    <div class="agent-avatar ${escapeHtml(safeTone)} ${escapeHtml(extraClass)}" title="${escapeHtml(name || safeTone)}">
      <span>${escapeHtml(agentAvatarSymbol(safeTone, name))}</span>
      <i></i>
    </div>
  `;
}

export function agentToneFromName(value = "", fallback = "lead") {
  const text = String(value || "").toLowerCase();
  if (text.includes("user") || text.includes("用户")) return "user";
  if (text.includes("planner") || text.includes("plan")) return "planner";
  if (text.includes("coder") || text.includes("code")) return "coder";
  if (text.includes("tester") || text.includes("test") || text.includes("verifier")) return "tester";
  if (text.includes("reviewer") || text.includes("review")) return "reviewer";
  if (text.includes("designer") || text.includes("design")) return "designer";
  if (text.includes("devops") || text.includes("deploy")) return "devops";
  if (text.includes("lead") || text.includes("supervisor")) return "lead";
  return fallback;
}

function agentAvatarSymbol(tone, name = "") {
  const symbols = {
    user: "U",
    lead: "L",
    planner: "P",
    coder: "</>",
    tester: "T",
    reviewer: "R",
    designer: "D",
    devops: "O",
  };
  return symbols[tone] || String(name || "A").slice(0, 1).toUpperCase();
}
