import React from "react";
import { PanelBottomClose, PanelBottomOpen } from "lucide-react";

function activeTabLabel(activeTab, tabs) {
  return tabs.find(([id]) => id === activeTab)?.[1] || "报告";
}

export default function EvidenceShell({ state, tabs, summary, content, onToggleBottom, onTabChange }) {
  const collapsed = state.layout?.bottomCollapsed;

  return (
    <section className={`panel bottom-panel ${collapsed ? "collapsed" : ""}`}>
      {!collapsed && (
        <div className="review-head">
          <div>
            <span>Evidence Drawer</span>
            <strong>{activeTabLabel(state.activeTab, tabs)}</strong>
          </div>
          <button className="icon-button subtle bottom-collapse-button" onClick={onToggleBottom} type="button" title="收起证据区"><PanelBottomClose size={16} /></button>
        </div>
      )}
      <div className={`bottom-tabs ${collapsed ? "compact" : ""}`}>
        {tabs.map(([id, label]) => (
          <button
            key={id}
            className={`tab-button ${state.activeTab === id ? "active" : ""}`}
            onClick={() => onTabChange?.(id)}
          >
            {label}
          </button>
        ))}
        {collapsed && <div className="bottom-summary compact">{summary}</div>}
        {collapsed && (
          <button className="icon-button subtle bottom-collapse-button" onClick={onToggleBottom} type="button" title="展开证据区"><PanelBottomOpen size={16} /></button>
        )}
      </div>
      {!collapsed && <div className="bottom-content">{content}</div>}
    </section>
  );
}
