export async function executeCommand(commandId, context) {
  const {
    state,
    ensureUiState,
    withBusyAction,
    startNewSession,
    shortPath,
    showToast,
    render,
    refreshWorkspaceData,
    loadWorkspaceOverview,
    saveLayoutPreference,
    setLayoutMode,
  } = context;

  ensureUiState().commandPaletteOpen = false;
  ensureUiState().commandQuery = "";

  if (commandId === "new-session") {
    await withBusyAction("new-session", async () => {
      await startNewSession();
      showToast("success", "新会话已创建", state.workspaceDir ? shortPath(state.workspaceDir) : "");
    });
    return;
  }

  if (commandId === "open-workspace") {
    ensureUiState().workspacePickerOpen = true;
    render();
    requestAnimationFrame(() => document.querySelector("#workspace-input")?.focus());
    return;
  }

  if (commandId === "sync") {
    await withBusyAction("sync-data", async () => {
      await refreshWorkspaceData({ allowEmpty: true, announce: true });
      await loadWorkspaceOverview();
      showToast("success", "同步完成", "运行、文件和能力数据已刷新。");
    });
    return;
  }

  if (commandId === "capabilities" || commandId === "mcp") {
    state.rightTab = "capabilities";
    state.layout.rightCollapsed = false;
    saveLayoutPreference();
    render();
    return;
  }

  if (["report", "diff", "timeline"].includes(commandId)) {
    state.activeTab = commandId;
    state.layout.bottomCollapsed = false;
    saveLayoutPreference();
    render();
    return;
  }

  if (commandId === "layout-focus") {
    setLayoutMode("focus");
    return;
  }

  if (commandId === "layout-workbench") {
    setLayoutMode("workbench");
    return;
  }

  if (commandId === "layout-review") {
    setLayoutMode("review");
  }
}
