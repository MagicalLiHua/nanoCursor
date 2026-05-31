import React, { useEffect, useState } from "react";
import { shortId, shortPath, statusLabel } from "../../core/format.js";
import { Timer, Command } from "lucide-react";

function RunTimer({ runStartedAt }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!runStartedAt) return;
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - runStartedAt) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [runStartedAt]);

  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  return (
    <span className="pill run-timer-pill">
      <span className="run-timer-icon"><Timer size={14} /></span>
      <strong>{mm}:{ss}</strong>
    </span>
  );
}

function WorkspacePickerPopover({ state, recentProjects, isActionBusy, onOpenWorkspace, onOpenRecent }) {
  return (
    <div className="workspace-popover">
      <form className="workspace-picker" id="workspace-form" onSubmit={onOpenWorkspace}>
        <label htmlFor="workspace-input">打开项目目录</label>
        <div className="workspace-input-row">
          <input id="workspace-input" defaultValue={state.workspaceInput || state.workspaceDir || ""} placeholder="/Users/you/project" />
          <button className={`button compact-button ${isActionBusy?.("open-workspace") ? "loading" : ""}`} type="submit" disabled={isActionBusy?.("open-workspace")}>
            {isActionBusy?.("open-workspace") ? "打开中" : "打开"}
          </button>
        </div>
      </form>
      <div className="workspace-recent">
        <span>最近项目</span>
        {recentProjects.length ? (
          recentProjects.map((item) => (
            <button key={item.path} className="workspace-recent-item" onClick={() => onOpenRecent?.(item.path)} type="button">
              <strong>{item.name || shortPath(item.path)}</strong>
              <small>{shortPath(item.path)}</small>
            </button>
          ))
        ) : (
          <div className="workspace-recent-empty">暂无最近项目</div>
        )}
      </div>
    </div>
  );
}

export default function Topbar({
  state, apiBase, isActionBusy, recentProjects,
  onToggleWorkspacePicker, onOpenWorkspace, onOpenRecent,
  onOpenCommandPalette, onNewSession, onSyncData, onCopyReport,
}) {
  const statusClass =
    state.status === "running" || state.status === "replaying" ? "running"
      : state.status === "failed" ? "error"
        : state.status === "completed" ? "success"
          : state.status === "waiting_approval" ? "warning"
            : state.status === "cancelling" ? "warning" : "";
  const workspacePath = state.workspaceDir || state.workspaceInput || "";
  const pickerOpen = Boolean(state.ui?.workspacePickerOpen);
  const displayApiBase = String(apiBase || "").replace(/^https?:\/\//, "");

  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">NC</div>
        <div>
          <strong>nanoCursor</strong>
          <span>Local agent workbench</span>
        </div>
      </div>
      <div className="topbar-main">
        <div className={`workspace-switcher ${pickerOpen ? "open" : ""}`}>
          <button className="workspace-chip" onClick={onToggleWorkspacePicker} type="button" title={workspacePath || "选择项目目录"}>
            <span>Project</span>
            <strong>{workspacePath ? shortPath(workspacePath) : "未打开项目目录"}</strong>
          </button>
          {pickerOpen && (
            <WorkspacePickerPopover state={state} recentProjects={recentProjects} isActionBusy={isActionBusy} onOpenWorkspace={onOpenWorkspace} onOpenRecent={onOpenRecent} />
          )}
        </div>
        <div className="topbar-meta">
          <span className={`pill status-pill status-${statusClass}`}><span className={`status-dot ${statusClass}`} /><strong>{statusLabel(state.status)}</strong></span>
          {["running", "waiting_approval", "cancelling"].includes(state.status) && state.runStartedAt && (
            <RunTimer runStartedAt={state.runStartedAt} />
          )}
          <details className="runtime-details">
            <summary>运行信息</summary>
            <div className="runtime-popover">
              <div><span>API</span><strong title={apiBase}>{displayApiBase}</strong></div>
              <div><span>Conversation</span><strong title={state.currentConversationId || "未创建"}>{shortId(state.currentConversationId)}</strong></div>
              <div><span>Thread</span><strong title={state.currentThreadId}>{shortId(state.currentThreadId, "未创建")}</strong></div>
            </div>
          </details>
        </div>
      </div>
      <div className="topbar-actions">
        <button className="button secondary command-trigger" onClick={onOpenCommandPalette} type="button" title="打开命令面板"><Command size={14} /> K</button>
        <button className={`button primary ${isActionBusy?.("new-session") ? "loading" : ""}`} onClick={onNewSession} disabled={isActionBusy?.("new-session")}>新会话</button>
        <button className={`button secondary ${isActionBusy?.("sync-data") ? "loading" : ""}`} onClick={onSyncData} disabled={isActionBusy?.("sync-data")}>{isActionBusy?.("sync-data") ? "同步中" : "刷新"}</button>
        <button className={`button secondary ${isActionBusy?.("copy-report") ? "loading" : ""}`} onClick={onCopyReport} disabled={isActionBusy?.("copy-report")}>复制报告</button>
      </div>
    </header>
  );
}
