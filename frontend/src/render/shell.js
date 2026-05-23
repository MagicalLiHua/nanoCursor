import { escapeHtml, shortId, shortPath, statusLabel } from "../core/format.js";

export function renderTopbar({
  state,
  apiBase,
  isActionBusy,
  renderWorkspacePickerPopover,
}) {
  const dotClass =
    state.status === "running" || state.status === "replaying"
      ? "running"
      : state.status === "failed"
        ? "error"
        : "";
  const workspacePath = state.workspaceDir || state.workspaceInput || "";
  const pickerOpen = Boolean(state.ui?.workspacePickerOpen);
  const displayApiBase = String(apiBase || "").replace(/^https?:\/\//, "");

  return `
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">NC</div>
        <div>
          <strong>nanoCursor</strong>
          <span>AI 工作台</span>
        </div>
      </div>
      <div class="topbar-main">
        <div class="workspace-switcher ${pickerOpen ? "open" : ""}">
          <button class="workspace-chip" data-action="toggle-workspace-picker" type="button" title="${escapeHtml(workspacePath || "选择项目目录")}">
            <span>Project</span>
            <strong>${escapeHtml(workspacePath ? shortPath(workspacePath) : "未打开项目目录")}</strong>
          </button>
          ${pickerOpen ? renderWorkspacePickerPopover() : ""}
        </div>
        <div class="topbar-meta">
          <span class="pill status-pill"><span class="status-dot ${dotClass}"></span><strong>${statusLabel(state.status)}</strong></span>
          <details class="runtime-details">
            <summary>运行信息</summary>
            <div class="runtime-popover">
              <div><span>API</span><strong title="${escapeHtml(apiBase)}">${escapeHtml(displayApiBase)}</strong></div>
              <div><span>Conversation</span><strong title="${escapeHtml(state.currentConversationId || "未创建")}">${escapeHtml(shortId(state.currentConversationId))}</strong></div>
              <div><span>Thread</span><strong title="${escapeHtml(state.currentThreadId)}">${escapeHtml(shortId(state.currentThreadId, "未创建"))}</strong></div>
            </div>
          </details>
        </div>
      </div>
      <div class="topbar-actions">
        <button class="button secondary command-trigger" data-action="open-command-palette" type="button" title="打开命令面板">⌘K</button>
        <button class="button ${isActionBusy("new-session") ? "loading" : ""}" data-action="new-session" ${isActionBusy("new-session") ? "disabled" : ""}>新会话</button>
        <button class="button secondary ${isActionBusy("sync-data") ? "loading" : ""}" data-action="sync-data" ${isActionBusy("sync-data") ? "disabled" : ""}>${isActionBusy("sync-data") ? "同步中" : "同步"}</button>
        <button class="button secondary ${isActionBusy("copy-report") ? "loading" : ""}" data-action="copy-report" ${isActionBusy("copy-report") ? "disabled" : ""}>复制报告</button>
      </div>
    </header>
  `;
}

export function renderWorkspacePickerPopover({
  state,
  recentProjects,
  isActionBusy,
}) {
  return `
    <div class="workspace-popover">
      <form class="workspace-picker" id="workspace-form">
        <label for="workspace-input">打开项目目录</label>
        <div class="workspace-input-row">
          <input id="workspace-input" value="${escapeHtml(state.workspaceInput || state.workspaceDir || "")}" placeholder="/Users/you/project" />
          <button class="button compact-button ${isActionBusy("open-workspace") ? "loading" : ""}" type="submit" ${isActionBusy("open-workspace") ? "disabled" : ""}>${isActionBusy("open-workspace") ? "打开中" : "打开"}</button>
        </div>
      </form>
      <div class="workspace-recent">
        <span>最近项目</span>
        ${
          recentProjects.length
            ? recentProjects
                .map(
                  (item) => `
                    <button class="workspace-recent-item" data-action="open-recent" data-path="${escapeHtml(item.path)}" type="button">
                      <strong>${escapeHtml(item.name || shortPath(item.path))}</strong>
                      <small>${escapeHtml(shortPath(item.path))}</small>
                    </button>
                  `,
                )
                .join("")
            : `<div class="workspace-recent-empty">暂无最近项目</div>`
        }
      </div>
    </div>
  `;
}
