export const STORAGE_KEYS = {
  apiBase: "nanocursor_api_base",
  workspaceDir: "nanocursor_workspace_dir",
  layout: "nanocursor_layout",
  layoutMode: "nanocursor_layout_mode",
  recommendationMuted: "nanocursor_recommendation_muted",
};

export const LEGACY_STORAGE_KEYS = {
  apiBase: "agenthub_api_base",
  workspaceDir: "agenthub_workspace_dir",
  layout: "agenthub_layout",
  layoutMode: "agenthub_layout_mode",
  recommendationMuted: "agenthub_recommendation_muted",
};

export const DEFAULT_LAYOUT = {
  sidebarCollapsed: false,
  rightCollapsed: false,
  bottomCollapsed: true,
};

export function getStorageValue(keyName) {
  const current = localStorage.getItem(STORAGE_KEYS[keyName]);
  if (current !== null) return current;
  const legacy = localStorage.getItem(LEGACY_STORAGE_KEYS[keyName]);
  if (legacy !== null) {
    localStorage.setItem(STORAGE_KEYS[keyName], legacy);
  }
  return legacy;
}

export function loadLayoutPreference() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEYS.layout) || "{}");
    return {
      ...DEFAULT_LAYOUT,
      sidebarCollapsed:
        typeof saved.sidebarCollapsed === "boolean" ? saved.sidebarCollapsed : DEFAULT_LAYOUT.sidebarCollapsed,
      rightCollapsed:
        typeof saved.rightCollapsed === "boolean" ? saved.rightCollapsed : DEFAULT_LAYOUT.rightCollapsed,
      bottomCollapsed:
        typeof saved.bottomCollapsed === "boolean" ? saved.bottomCollapsed : DEFAULT_LAYOUT.bottomCollapsed,
    };
  } catch {
    return { ...DEFAULT_LAYOUT };
  }
}

export function saveLayoutPreference(layout) {
  try {
    localStorage.setItem(STORAGE_KEYS.layout, JSON.stringify({ ...DEFAULT_LAYOUT, ...(layout || {}) }));
  } catch {
    // Ignore storage failures; the layout should still work for the current session.
  }
}

export function saveLayoutMode(mode) {
  try {
    localStorage.setItem(STORAGE_KEYS.layoutMode, mode);
  } catch {
    // Layout mode persistence is nice-to-have.
  }
}
