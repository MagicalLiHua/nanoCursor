import { useEffect, useCallback } from "react";
import useStore from "./store/index.js";
import { useSSE } from "./hooks/useSSE.js";
import { useBootstrap } from "./hooks/useBootstrap.js";

import Sidebar from "./components/sidebar/Sidebar.jsx";
import ChatPanel from "./components/chat/ChatPanel.jsx";
import ContextPanel, { buildRightPanelTabs, resolveRightTab } from "./components/context/ContextPanel.jsx";
import Tasks from "./components/context/Tasks.jsx";
import Team from "./components/context/Team.jsx";
import Metrics from "./components/context/Metrics.jsx";
import EvidenceShell from "./components/evidence/EvidenceShell.jsx";
import Report from "./components/evidence/Report.jsx";
import DiffView from "./components/evidence/DiffView.jsx";
import Timeline from "./components/evidence/Timeline.jsx";
import Recovery from "./components/evidence/Recovery.jsx";
import Artifacts from "./components/evidence/Artifacts.jsx";
import Toast from "./components/shared/Toast.jsx";
import CommandPalette from "./components/shared/CommandPalette.jsx";
import SettingsPage from "./components/shared/SettingsPage.jsx";

function layoutClass(layout, ui) {
  const mode = ["focus", "workbench", "review"].includes(ui?.layoutMode) ? ui.layoutMode : "workbench";
  const classes = ["workspace", `layout-${mode}`];
  if (layout?.sidebarCollapsed) classes.push("sidebar-collapsed");
  if (layout?.rightCollapsed) classes.push("right-collapsed");
  if (layout?.bottomCollapsed) classes.push("bottom-collapsed");
  return classes.join(" ");
}

export default function App() {
  const state = useStore();
  const {
    toggleSidebar, toggleBottom,
    showToast, clearToast,
    setState, addMessage, addTimelineEvent,
    cancelCurrentRun, resetRunView,
    submitApprovalDecision,
    runPrompt, restoreRun, startNewSession, openWorkspace,
  } = useStore();

  useSSE();
  useBootstrap();

  // Keyboard shortcuts
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setState((s) => ({ ui: { ...s.ui, commandPaletteOpen: !s.ui?.commandPaletteOpen } }));
      }
      if (e.key === "Escape") {
        if (state.ui?.commandPaletteOpen) {
          e.preventDefault();
          setState((s) => ({ ui: { ...s.ui, commandPaletteOpen: false } }));
        } else if (state.ui?.workspacePickerOpen) {
          e.preventDefault();
          setState((s) => ({ ui: { ...s.ui, workspacePickerOpen: false } }));
        } else if (!state.layout?.bottomCollapsed) {
          e.preventDefault();
          toggleBottom();
        }
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [state.ui?.commandPaletteOpen, state.ui?.workspacePickerOpen, state.layout?.bottomCollapsed]);

  // Derive layout
  const mainClass = layoutClass(state.layout, state.ui);
  const isRunning = ["running", "waiting_approval", "cancelling"].includes(state.status);
  const ephemeralCount = isRunning
    ? (state.ephemeralAgents?.active_count || 0) + (state.ephemeralAgents?.suggestions?.length || 0)
    : 0;
  const rightTabs = buildRightPanelTabs({ state, ephemeralCount });
  const activeRightTab = resolveRightTab(state.rightTab, rightTabs);

  // Event handlers
  const handleToggleSidebar = useCallback(() => toggleSidebar(), []);
  const handleToggleBottom = useCallback(() => toggleBottom(), []);
  const handleLeftTabChange = useCallback((tab) => setState({ leftTab: tab }), []);
  const handleRightTabChange = useCallback((tab) => setState({ rightTab: tab }), []);
  const handleBottomTabChange = useCallback((tab) => setState({ activeTab: tab }), []);

  const handleNewSession = useCallback(() => {
    startNewSession();
  }, [startNewSession]);

  const handleOpenWorkspace = useCallback((e) => {
    e?.preventDefault();
    openWorkspace();
  }, [openWorkspace]);

  const handleWorkspaceInputChange = useCallback((value) => {
    setState({ workspaceInput: value });
  }, []);

  const handleOpenCommandPalette = useCallback(() => {
    setState((s) => ({ ui: { ...s.ui, commandPaletteOpen: true } }));
  }, []);

  const handleOpenSettings = useCallback(() => {
    setState((s) => ({ ui: { ...s.ui, settingsOpen: true } }));
  }, []);

  const handleCloseSettings = useCallback(() => {
    setState((s) => ({ ui: { ...s.ui, settingsOpen: false } }));
  }, []);

  const handleCloseCommandPalette = useCallback(() => {
    setState((s) => ({ ui: { ...s.ui, commandPaletteOpen: false } }));
  }, []);

  const handleRunCommand = useCallback((commandId) => {
    setState((s) => ({ ui: { ...s.ui, commandPaletteOpen: false } }));
    showToast({ title: "执行命令", content: commandId, kind: "info" });
  }, [showToast]);

  const handleCommandQueryChange = useCallback((query) => {
    setState((s) => ({ ui: { ...s.ui, commandQuery: query } }));
  }, []);

  const handleSubmitPrompt = useCallback((eOrText) => {
    if (typeof eOrText === "string") {
      if (eOrText.trim()) runPrompt(eOrText.trim());
      return;
    }
    eOrText?.preventDefault();
    const form = eOrText?.target;
    const input = form?.querySelector("#prompt-input");
    const prompt = input?.value?.trim();
    if (!prompt) return;
    runPrompt(prompt);
  }, [runPrompt]);

  const handleCancelRun = useCallback(() => {
    cancelCurrentRun();
  }, [cancelCurrentRun]);

  const handleFillPrompt = useCallback((text) => {
    setState({ prompt: text });
  }, []);

  const handleApprovalDecision = useCallback((decision) => {
    submitApprovalDecision(decision);
  }, [submitApprovalDecision]);

  const handleApprovalCommentChange = useCallback((comment) => {
    setState({ approvalComment: comment });
  }, []);

  const handleSelectDiffFile = useCallback((path) => {
    setState({ selectedDiffFile: path });
  }, []);

  const handleToggleCompleted = useCallback(() => {
    setState((s) => ({ showCompletedTasks: !s.showCompletedTasks }));
  }, []);

  // Right panel content
  function renderRightContent() {
    if (activeRightTab === "tasks") return <Tasks state={state} onToggleCompleted={handleToggleCompleted} />;
    if (activeRightTab === "team") return <Team state={state} />;
    if (activeRightTab === "metrics") return <Metrics state={state} />;
    return null;
  }

  // Bottom panel content
  const bottomTabs = [
    ["report", "报告"],
    ["diff", "Diff"],
    ["timeline", "事件"],
    ["recovery", "恢复"],
    ["artifacts", "交付物"],
  ];

  function renderBottomContent() {
    if (state.activeTab === "report") return <Report state={state} />;
    if (state.activeTab === "diff") return <DiffView state={state} onSelectFile={handleSelectDiffFile} />;
    if (state.activeTab === "timeline") return <Timeline state={state} />;
    if (state.activeTab === "recovery") return <Recovery center={state.recoveryCenter} />;
    if (state.activeTab === "artifacts") return <Artifacts center={state.artifactCenter} />;
    return null;
  }

  return (
    <div className="app-shell">
      <main className={mainClass}>
        <Sidebar
          state={state}
          onToggleSidebar={handleToggleSidebar}
          onNewSession={handleNewSession}
          onSelectRun={restoreRun}
          onOpenWorkspace={handleOpenWorkspace}
          onWorkspaceInputChange={handleWorkspaceInputChange}
          onOpenSettings={handleOpenSettings}
        />
        <ChatPanel
          state={state}
          isActionBusy={() => false}
          onSubmit={handleSubmitPrompt}
          onCancel={handleCancelRun}
          onFillPrompt={handleFillPrompt}
          onApprovalDecision={handleApprovalDecision}
          onApprovalCommentChange={handleApprovalCommentChange}
        />
        <ContextPanel
          state={state}
          tabs={rightTabs}
          activeTab={activeRightTab}
          content={renderRightContent()}
          onTabChange={handleRightTabChange}
        />
        <EvidenceShell
          state={state}
          tabs={bottomTabs}
          summary=""
          content={renderBottomContent()}
          onToggleBottom={handleToggleBottom}
          onTabChange={handleBottomTabChange}
        />
      </main>
      <Toast toast={state.ui?.toast} />
      <CommandPalette
        ui={state.ui}
        commands={[]}
        onClose={handleCloseCommandPalette}
        onRunCommand={handleRunCommand}
        onQueryChange={handleCommandQueryChange}
      />
      {state.ui?.settingsOpen && (
        <SettingsPage onClose={handleCloseSettings} />
      )}
    </div>
  );
}
