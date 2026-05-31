import { saveLayoutMode, saveLayoutPreference as persistLayoutPreferenceValue } from "../../core/storage.js";

export function createLayoutActions(set, get) {
  function ensureUiState() {
    const state = get();
    const ui = {
      busyActions: {},
      toast: null,
      workspacePickerOpen: false,
      recommendationExpanded: false,
      commandPaletteOpen: false,
      commandQuery: "",
      layoutMode: "workbench",
      ...(state.ui || {}),
    };
    return ui;
  }

  function currentLayoutMode() {
    const mode = ensureUiState().layoutMode;
    return ["focus", "workbench", "review"].includes(mode) ? mode : "workbench";
  }

  function layoutClass() {
    const state = get();
    const mode = currentLayoutMode();
    const classes = ["workspace", `layout-${mode}`];
    if (state.layout?.sidebarCollapsed) classes.push("sidebar-collapsed");
    if (state.layout?.rightCollapsed) classes.push("right-collapsed");
    if (state.layout?.bottomCollapsed) classes.push("bottom-collapsed");
    return classes.join(" ");
  }

  function setLayoutMode(mode) {
    const nextMode = ["focus", "workbench", "review"].includes(mode) ? mode : "workbench";
    const state = get();
    const ui = { ...ensureUiState(), layoutMode: nextMode };

    let layout = { ...(state.layout || {}) };
    if (nextMode === "focus") {
      layout = { ...layout, sidebarCollapsed: true, rightCollapsed: true, bottomCollapsed: true };
    } else if (nextMode === "review") {
      layout = { ...layout, sidebarCollapsed: true, rightCollapsed: true, bottomCollapsed: false };
    } else {
      layout = { ...layout, sidebarCollapsed: false, rightCollapsed: false, bottomCollapsed: true };
    }

    saveLayoutMode(nextMode);
    persistLayoutPreferenceValue(layout);
    set({ ui, layout });
  }

  function toggleSidebar() {
    const state = get();
    const layout = { ...(state.layout || {}), sidebarCollapsed: !state.layout?.sidebarCollapsed };
    persistLayoutPreferenceValue(layout);
    set({ layout });
  }

  function toggleRight() {
    const state = get();
    const layout = { ...(state.layout || {}), rightCollapsed: !state.layout?.rightCollapsed };
    persistLayoutPreferenceValue(layout);
    set({ layout });
  }

  function toggleBottom() {
    const state = get();
    const layout = { ...(state.layout || {}), bottomCollapsed: !state.layout?.bottomCollapsed };
    persistLayoutPreferenceValue(layout);
    set({ layout });
  }

  function setLayoutMode_ensureUiState() {
    return ensureUiState();
  }

  return {
    ensureUiState: setLayoutMode_ensureUiState,
    currentLayoutMode,
    layoutClass,
    setLayoutMode,
    toggleSidebar,
    toggleRight,
    toggleBottom,
  };
}
