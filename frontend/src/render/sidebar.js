import { escapeHtml } from "../core/format.js";

export function buildSidebarTabs(state) {
  return [
    ["project", "项目", state.projectOverview?.summary?.recent_run_count ?? 0],
    ["runs", "会话", state.runs.length],
    ["files", "文件", state.files.length],
  ];
}

export function renderSidebar({ state, tabs, content }) {
  if (state.layout?.sidebarCollapsed) {
    return `
      <aside class="sidebar collapsed-rail">
        <section class="panel rail-panel">
          <button class="rail-toggle" data-action="toggle-sidebar" title="展开左侧栏">›</button>
          ${tabs
            .map(
              ([id, label, count]) => `
                <button class="rail-nav-button ${state.leftTab === id ? "active" : ""}" data-action="side-nav" data-side="left" data-tab="${id}" title="${label}">
                  <strong>${escapeHtml(count)}</strong>
                  <span>${escapeHtml(label)}</span>
                </button>
              `,
            )
            .join("")}
        </section>
      </aside>
    `;
  }

  const active = sidebarActiveMeta(state);

  return `
    <aside class="sidebar">
      <section class="panel sidebar-section">
        <div class="panel-header sidebar-head">
          <div>
            <span>Workspace</span>
            <h2 class="panel-title">${escapeHtml(active.label)}</h2>
          </div>
          <div class="panel-actions">
            <span class="panel-subtitle">${escapeHtml(active.count)} ${active.unit}</span>
            ${state.leftTab === "runs" ? `<button class="icon-button" data-action="new-session" title="新建会话">+</button>` : ""}
            <button class="icon-button" data-action="toggle-sidebar" title="收起左侧栏">‹</button>
          </div>
        </div>
        <div class="side-tabs">
          ${tabs
            .map(
              ([id, label]) =>
                `<button class="tab-button ${state.leftTab === id ? "active" : ""}" data-action="left-tab" data-tab="${id}">${escapeHtml(label)}</button>`,
            )
            .join("")}
        </div>
        <div class="content-scroll ${content.className}">
          ${content.html}
        </div>
      </section>
    </aside>
  `;
}

function sidebarActiveMeta(state) {
  if (state.leftTab === "files") {
    return { label: "文件", count: state.files.length, unit: "个" };
  }
  if (state.leftTab === "project") {
    return {
      label: "项目",
      count: state.projectOverview?.summary?.recent_run_count ?? 0,
      unit: "项",
    };
  }
  return { label: "会话", count: state.runs.length, unit: "条" };
}
