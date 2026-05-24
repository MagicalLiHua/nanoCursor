export function createLayoutController({ state, render, persistLayoutPreference, saveLayoutMode }) {
  let globalShortcutsBound = false;

  function saveLayoutPreference() {
    persistLayoutPreference(state.layout);
  }

  function ensureUiState() {
    state.ui = {
      busyActions: {},
      toast: null,
      workspacePickerOpen: false,
      recommendationExpanded: false,
      commandPaletteOpen: false,
      commandQuery: "",
      layoutMode: "workbench",
      ...(state.ui || {}),
    };
    return state.ui;
  }

  function currentLayoutMode() {
    const mode = ensureUiState().layoutMode;
    return ["focus", "workbench", "review"].includes(mode) ? mode : "workbench";
  }

  function layoutClass() {
    const mode = currentLayoutMode();
    const classes = ["workspace", `layout-${mode}`];
    if (state.layout?.sidebarCollapsed) classes.push("sidebar-collapsed");
    if (state.layout?.rightCollapsed) classes.push("right-collapsed");
    if (state.layout?.bottomCollapsed) classes.push("bottom-collapsed");
    return classes.join(" ");
  }

  function setLayoutMode(mode) {
    const nextMode = ["focus", "workbench", "review"].includes(mode) ? mode : "workbench";
    const ui = ensureUiState();
    ui.layoutMode = nextMode;
    if (nextMode === "focus") {
      state.layout.sidebarCollapsed = true;
      state.layout.rightCollapsed = true;
      state.layout.bottomCollapsed = true;
    } else if (nextMode === "review") {
      state.layout.sidebarCollapsed = true;
      state.layout.rightCollapsed = true;
      state.layout.bottomCollapsed = false;
    } else {
      state.layout.sidebarCollapsed = false;
      state.layout.rightCollapsed = false;
      state.layout.bottomCollapsed = true;
    }
    saveLayoutMode(nextMode);
    saveLayoutPreference();
    render();
  }

  function captureFocusedField() {
    const active = document.activeElement;
    if (!active || !["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)) return null;
    return {
      id: active.id,
      value: active.value,
      selectionStart: active.selectionStart,
      selectionEnd: active.selectionEnd,
    };
  }

  function restoreFocusedField(snapshot) {
    if (!snapshot?.id) return;
    const field = document.querySelector(`#${CSS.escape(snapshot.id)}`);
    if (!field) return;
    field.focus({ preventScroll: true });
    if (typeof snapshot.value === "string" && field.value !== snapshot.value) {
      field.value = snapshot.value;
    }
    if (typeof field.setSelectionRange === "function" && snapshot.selectionStart !== null) {
      field.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd ?? snapshot.selectionStart);
    }
  }

  function bindGlobalShortcutsOnce({ openCommandPalette, closeCommandPalette }) {
    if (globalShortcutsBound) return;
    document.addEventListener("keydown", (event) => {
      const target = event.target;
      const isField = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openCommandPalette();
        return;
      }
      if (event.key !== "Escape") return;
      if (ensureUiState().commandPaletteOpen) {
        event.preventDefault();
        closeCommandPalette();
        return;
      }
      if (ensureUiState().workspacePickerOpen) {
        event.preventDefault();
        ensureUiState().workspacePickerOpen = false;
        render();
        return;
      }
      if (!state.layout?.bottomCollapsed && !isField) {
        event.preventDefault();
        state.layout.bottomCollapsed = true;
        saveLayoutPreference();
        render();
      }
    });
    globalShortcutsBound = true;
  }

  return {
    bindGlobalShortcutsOnce,
    captureFocusedField,
    ensureUiState,
    layoutClass,
    restoreFocusedField,
    saveLayoutPreference,
    setLayoutMode,
  };
}
