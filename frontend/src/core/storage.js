export const STORAGE_KEYS = {
  apiBase: "nanocursor_api_base",
  workspaceDir: "nanocursor_workspace_dir",
  layout: "nanocursor_layout",
  layoutMode: "nanocursor_layout_mode",
  recommendationMuted: "nanocursor_recommendation_muted",
  activeSessions: "nanocursor_active_sessions",
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

function storageOrDefault(storage) {
  if (storage) return storage;
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

function workspaceKey(workspaceDir) {
  return String(workspaceDir || "").trim();
}

export function loadActiveSession(workspaceDir, storage) {
  const target = storageOrDefault(storage);
  const key = workspaceKey(workspaceDir);
  if (!target || !key) return null;
  try {
    const sessions = JSON.parse(target.getItem(STORAGE_KEYS.activeSessions) || "{}");
    const saved = sessions?.[key];
    if (!saved || typeof saved !== "object") return null;
    const conversationId = String(saved.conversationId || "").trim();
    const threadId = String(saved.threadId || "").trim();
    if (!conversationId && (!threadId || threadId === "pending")) return null;
    return { workspaceDir: key, conversationId, threadId };
  } catch {
    return null;
  }
}

export function saveActiveSession({ workspaceDir, conversationId = "", threadId = "" } = {}, storage) {
  const target = storageOrDefault(storage);
  const key = workspaceKey(workspaceDir);
  const normalizedThreadId = String(threadId || "").trim();
  const normalizedConversationId = String(conversationId || "").trim();
  if (!target || !key || (!normalizedConversationId && (!normalizedThreadId || normalizedThreadId === "pending"))) {
    return;
  }
  try {
    const sessions = JSON.parse(target.getItem(STORAGE_KEYS.activeSessions) || "{}");
    sessions[key] = {
      conversationId: normalizedConversationId,
      threadId: normalizedThreadId === "pending" ? "" : normalizedThreadId,
    };
    target.setItem(STORAGE_KEYS.activeSessions, JSON.stringify(sessions));
  } catch {
    // Session persistence is best effort; backend state remains authoritative.
  }
}

export function clearActiveSession(workspaceDir, storage) {
  const target = storageOrDefault(storage);
  const key = workspaceKey(workspaceDir);
  if (!target || !key) return;
  try {
    const sessions = JSON.parse(target.getItem(STORAGE_KEYS.activeSessions) || "{}");
    delete sessions[key];
    target.setItem(STORAGE_KEYS.activeSessions, JSON.stringify(sessions));
  } catch {
    // Ignore unavailable or malformed storage.
  }
}
