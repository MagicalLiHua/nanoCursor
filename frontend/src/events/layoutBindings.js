export function bindLayoutEvents(context) {
  const { state, render, saveLayoutPreference, saveWorkspaceSettings } = context;

  document.querySelectorAll("[data-action='right-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      state.rightTab = button.dataset.tab;
      render();
    });
  });

  document.querySelectorAll("[data-action='left-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      state.leftTab = button.dataset.tab;
      render();
    });
  });

  document.querySelectorAll("[data-action='side-nav']").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.side === "left") {
        state.leftTab = button.dataset.tab || state.leftTab;
        state.layout.sidebarCollapsed = false;
      } else {
        state.rightTab = button.dataset.tab || state.rightTab;
        state.layout.rightCollapsed = false;
      }
      saveLayoutPreference();
      render();
    });
  });

  document.querySelectorAll("[data-action='bottom-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      state.layout.bottomCollapsed = false;
      saveLayoutPreference();
      render();
    });
  });

  document.querySelector("[data-action='toggle-sidebar']")?.addEventListener("click", () => {
    state.layout.sidebarCollapsed = !state.layout.sidebarCollapsed;
    saveLayoutPreference();
    render();
  });

  document.querySelector("[data-action='toggle-right']")?.addEventListener("click", () => {
    state.layout.rightCollapsed = !state.layout.rightCollapsed;
    saveLayoutPreference();
    render();
  });

  document.querySelector("[data-action='toggle-bottom']")?.addEventListener("click", () => {
    state.layout.bottomCollapsed = !state.layout.bottomCollapsed;
    saveLayoutPreference();
    render();
  });

  document.querySelectorAll("[data-action='goto-capabilities']").forEach((button) => {
    button.addEventListener("click", () => {
      state.rightTab = "capabilities";
      state.layout.rightCollapsed = false;
      saveLayoutPreference();
      render();
    });
  });

  document.querySelector("[data-action='save-settings']")?.addEventListener("click", async () => {
    await saveWorkspaceSettings();
  });
}
