import { escapeHtml } from "../core/format.js";

export function renderBottomPanel({ state, tabs, summary, content }) {
  const collapsed = state.layout?.bottomCollapsed;

  return `
    <section class="panel bottom-panel ${collapsed ? "collapsed" : ""}">
      ${
        collapsed
          ? ""
          : `<div class="review-head">
              <div>
                <span>Review Drawer</span>
                <strong>${escapeHtml(activeTabLabel(state.activeTab, tabs))}</strong>
              </div>
              <button class="button secondary compact-button bottom-collapse-button" data-action="toggle-bottom" type="button" title="收起证据区">收起</button>
            </div>`
      }
      <div class="bottom-tabs ${collapsed ? "compact" : ""}">
        ${tabs
          .map(
            ([id, label]) =>
              `<button class="tab-button ${state.activeTab === id ? "active" : ""}" data-action="bottom-tab" data-tab="${id}">${label}</button>`,
          )
          .join("")}
        ${collapsed ? `<div class="bottom-summary compact">${summary}</div>` : ""}
        ${collapsed ? `<button class="button secondary compact-button bottom-collapse-button" data-action="toggle-bottom" type="button" title="展开证据区">展开审查</button>` : ""}
      </div>
      ${
        collapsed
          ? ""
          : `<div class="bottom-content">${content}</div>`
      }
    </section>
  `;
}

function activeTabLabel(activeTab, tabs) {
  return tabs.find(([id]) => id === activeTab)?.[1] || "报告";
}
