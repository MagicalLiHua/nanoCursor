import React from "react";
import { ListTodo, Users, BarChart3 } from "lucide-react";

function normalizeTaskTitle(title = "") {
  return String(title).replace(/\s+/g, "").replace(/[：:]/g, ":").trim();
}

function visibleTaskCount(tasks = []) {
  return tasks.filter((task) => {
    const hasContent = Boolean(String(task?.title || "").trim() || String(task?.description || "").trim());
    if (!hasContent || task.source === "execution_plan") return hasContent;
    const match = normalizeTaskTitle(task.title).match(/^阶段\d+\s*[:：]\s*(.+)$/);
    if (!match) return true;
    return !tasks.some(
      (c) => c.source === "execution_plan" && normalizeTaskTitle(c.title) === normalizeTaskTitle(match[1]),
    );
  }).length;
}

export function buildRightPanelTabs({ state, ephemeralCount }) {
  const taskCount = visibleTaskCount(state.tasks);
  return [
    { id: "tasks", icon: ListTodo, label: "任务", count: taskCount },
    { id: "team", icon: Users, label: "团队", count: state.team.length + ephemeralCount },
    { id: "metrics", icon: BarChart3, label: "指标", count: state.metrics.toolCalls },
  ];
}

export function resolveRightTab(currentTab, tabs) {
  const tabIds = tabs.map((t) => t.id);
  return tabIds.includes(currentTab) ? currentTab : tabs[0].id;
}

export default function ContextPanel({ state, tabs, activeTab, content, onTabChange }) {
  return (
    <aside className="right-panel right-sidebar">
      {/* Icon Rail */}
      <div className="right-rail-v2">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`rail-icon ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => onTabChange?.(tab.id)}
            title={tab.label}
            type="button"
          >
            <tab.icon size={20} />
            {tab.count > 0 && <span className="rail-badge">{tab.count > 99 ? "99+" : tab.count}</span>}
          </button>
        ))}
      </div>

      {/* Expanded Panel */}
      <div className="right-panel-v2">
        <div className="right-panel-header">
          <h3>{tabs.find((t) => t.id === activeTab)?.label || "任务"}</h3>
        </div>
        <div className="right-panel-body">
          {content}
        </div>
      </div>
    </aside>
  );
}
