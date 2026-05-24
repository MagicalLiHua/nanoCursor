export function bindWorkspaceEvents(context) {
  const {
    state,
    render,
    withBusyAction,
    openWorkspace,
    startNewSession,
    showToast,
    shortPath,
    restoreRun,
    refreshWorkspaceData,
    loadWorkspaceOverview,
    addTimelineEvent,
  } = context;

  document.querySelector("[data-action='toggle-workspace-picker']")?.addEventListener("click", () => {
    state.ui = state.ui || { busyActions: {}, toast: null, workspacePickerOpen: false };
    state.ui.workspacePickerOpen = !state.ui.workspacePickerOpen;
    render();
    if (state.ui.workspacePickerOpen) {
      requestAnimationFrame(() => document.querySelector("#workspace-input")?.focus());
    }
  });

  document.querySelectorAll("[data-action='open-recent']").forEach((button) => {
    button.addEventListener("click", async () => {
      const path = button.dataset.path;
      if (!path) return;
      state.workspaceInput = path;
      await withBusyAction("open-workspace", openWorkspace);
    });
  });

  document.querySelectorAll("[data-action='new-session']").forEach((button) => {
    button.addEventListener("click", async () => {
      await withBusyAction("new-session", async () => {
        await startNewSession();
        showToast("success", "新会话已创建", state.workspaceDir ? shortPath(state.workspaceDir) : "");
      });
    });
  });

  document.querySelector("#workspace-input")?.addEventListener("input", (event) => {
    state.workspaceInput = event.target.value;
  });

  document.querySelector("#workspace-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      state.ui.workspacePickerOpen = false;
      render();
    }
  });

  document.querySelector("#workspace-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await withBusyAction("open-workspace", openWorkspace);
  });

  document.querySelectorAll("[data-action='select-run']").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      showToast("success", "正在恢复运行", shortPath(button.dataset.runId || "历史会话"));
      await restoreRun(button.dataset.runId, { force: true });
    });
  });

  document.querySelector("[data-action='sync-data']")?.addEventListener("click", async () => {
    await withBusyAction("sync-data", async () => {
      await refreshWorkspaceData({ allowEmpty: true, announce: true });
      await loadWorkspaceOverview();
      showToast("success", "同步完成", "运行、文件和能力数据已刷新。");
    });
  });

  document.querySelector("[data-action='refresh-project-overview']")?.addEventListener("click", async () => {
    await loadWorkspaceOverview();
    addTimelineEvent({
      type: "workspace_overview",
      title: "项目概览已同步",
      content: state.projectOverview?.workspace_dir || state.workspaceDir || "当前工作区",
    });
  });
}
