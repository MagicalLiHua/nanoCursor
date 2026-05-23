import { escapeHtml } from "../core/format.js";

export function getRightPanelMode(status) {
  if (status === "failed") return "recovery";
  if (status === "running") return "execution";
  if (status === "completed") return "delivery";
  return "project";
}

export function buildRightPanelTabs({ state, ephemeralCount }) {
  const mode = getRightPanelMode(state.status);
  const tabSets = {
    project: [
      ["capabilities", "能力", state.capabilityHub?.summary?.total ?? 0],
      ["team", "团队", state.team.length],
      ["ephemeral", "临时", ephemeralCount],
      ["preferences", "偏好", state.memoryProfile?.preference_count ?? 0],
    ],
    execution: [
      ["tasks", "任务", state.tasks.length],
      ["team", "团队", state.team.length],
      ["ephemeral", "临时", ephemeralCount],
      ["metrics", "指标", state.metrics.toolCalls],
    ],
    delivery: [
      ["tasks", "任务", state.tasks.length],
      ["ephemeral", "临时", ephemeralCount],
      ["capabilities", "能力", state.capabilityHub?.summary?.total ?? 0],
      ["preferences", "偏好", state.memoryProfile?.preference_count ?? 0],
    ],
    recovery: [
      ["recovery", "恢复", state.recoveryCenter?.summary?.risk_count ?? 0],
      ["tasks", "任务", state.tasks.length],
      ["team", "团队", state.team.length],
      ["ephemeral", "临时", ephemeralCount],
    ],
  };
  return [...(tabSets[mode] || tabSets.project), ["settings", "设置", 0]];
}

export function resolveRightTab(currentTab, tabs) {
  const tabIds = tabs.map(([id]) => id);
  return tabIds.includes(currentTab) ? currentTab : tabs[0][0];
}

export function renderRightPanel({ state, tabs, activeTab, content }) {
  if (state.layout?.rightCollapsed) {
    return `
      <aside class="panel right-panel right-rail">
        <button class="rail-toggle" data-action="toggle-right" title="展开右侧栏">‹</button>
        ${tabs
          .map(
            ([id, label, count]) => `
              <button class="rail-nav-button ${activeTab === id ? "active" : ""}" data-action="side-nav" data-side="right" data-tab="${id}" title="${label}">
                <strong>${escapeHtml(count)}</strong>
                <span>${escapeHtml(label)}</span>
              </button>
            `,
          )
          .join("")}
      </aside>
    `;
  }

  return `
    <aside class="panel right-panel">
      <div class="context-head">
        <div>
          <span>上下文</span>
          <strong>${escapeHtml(activeTabLabel(activeTab, tabs))}</strong>
        </div>
        <button class="icon-button subtle" data-action="toggle-right" title="收起右侧栏" type="button">›</button>
      </div>
      <div class="right-tabs">
        ${tabs
          .map(
            ([id, label]) =>
              `<button class="tab-button ${activeTab === id ? "active" : ""}" data-action="right-tab" data-tab="${id}">${label}</button>`,
          )
          .join("")}
      </div>
      <div class="content-scroll">
        ${content}
      </div>
    </aside>
  `;
}

function activeTabLabel(activeTab, tabs) {
  return tabs.find(([id]) => id === activeTab)?.[1] || "项目";
}
