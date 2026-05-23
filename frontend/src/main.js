import {
  STORAGE_KEYS,
  getStorageValue,
  saveLayoutMode,
  saveLayoutPreference as persistLayoutPreference,
} from "./core/storage.js";
import { createApiClient } from "./core/apiClient.js";
import {
  approvalDecisionLabel,
  escapeHtml,
  formatTime,
  nowTime,
  runTitle,
  shortId,
  shortPath,
  statusLabel,
} from "./core/format.js";
import { parseUnifiedDiff } from "./core/diff.js";
import { filterCommandItems } from "./ui/commands.js";
import { renderCommandPalette as renderCommandPaletteView } from "./ui/commandPalette.js";
import { renderToast as renderToastView } from "./ui/toast.js";
import { bindDomEvents } from "./events/domBindings.js";
import {
  agentToneFromName,
  renderChat as renderChatView,
} from "./render/chat.js";
import {
  buildRightPanelTabs,
  renderRightPanel as renderRightPanelView,
  resolveRightTab,
} from "./render/contextPanel.js";
import {
  renderEphemeralAgents as renderEphemeralAgentsView,
  renderTasks as renderTasksView,
  renderTeam as renderTeamView,
} from "./render/contextContent.js";
import {
  renderBenchmarks as renderBenchmarksView,
  renderMetrics as renderMetricsView,
  renderPreferences as renderPreferencesView,
  renderWorkspaceSettings as renderWorkspaceSettingsView,
} from "./render/contextExtras.js";
import { renderBottomPanel as renderBottomPanelView } from "./render/evidenceShell.js";
import {
  renderDiffView as renderDiffViewContent,
  renderPreview as renderPreviewContent,
  renderTimeline as renderTimelineContent,
} from "./render/evidenceContent.js";
import { renderCapabilities as renderCapabilitiesView } from "./render/capabilities.js";
import { renderArtifacts as renderArtifactsView } from "./render/artifacts.js";
import { renderRecovery as renderRecoveryView } from "./render/recovery.js";
import { renderReport as renderReportView } from "./render/report.js";
import {
  renderTopbar as renderTopbarView,
  renderWorkspacePickerPopover as renderWorkspacePickerPopoverView,
} from "./render/shell.js";
import {
  buildSidebarTabs,
  renderSidebar as renderSidebarView,
} from "./render/sidebar.js";
import {
  renderRecentProjectsHtml,
  renderSidebarContent as renderSidebarContentView,
} from "./render/workspaceNav.js";
import { executeCommand as executeCommandAction } from "./services/commandExecutor.js";
import {
  normalizeMcpConfig as normalizeMcpConfigPayload,
  parseMcpArgs,
  parseMcpEnvKeys,
} from "./services/mcpConfig.js";
import {
  inferLocalCapabilityRecommendation as inferLocalCapabilityRecommendationValue,
  normalizeCapabilityRecommendation,
} from "./services/capabilityRecommendation.js";
import {
  normalizeApprovalTasks,
  submitApprovalDecision as submitApprovalDecisionAction,
} from "./services/approvalService.js";
import {
  handleAgentEvent as handleAgentEventAction,
  updateCurrentRunStatus as updateCurrentRunStatusAction,
} from "./services/runEventHandler.js";
import { buildReportText as buildReportTextValue } from "./services/reportText.js";
import {
  loadWorkspaceSettings as loadWorkspaceSettingsValue,
  saveWorkspaceSettings as saveWorkspaceSettingsValue,
} from "./services/workspaceSettings.js";
import { demoState } from "./state/demoState.js";
import {
  fileType,
  mapBackendTasks as mapBackendTasksValue,
  mapBackendTeam as mapBackendTeamValue,
  mapConversationItem as mapConversationItemValue,
  mapRunHistoryItem as mapRunHistoryItemValue,
  normalizeTask as normalizeTaskValue,
  stageIdFromTaskId,
  tasksFromExecutionPlan as tasksFromExecutionPlanValue,
} from "./state/mappers.js";
import {
  blankApproval as blankApprovalValue,
  blankArtifactCenter,
  blankEphemeralAgents,
  blankRecoveryCenter,
  blankReport,
  normalizeEphemeralAgentsResult,
} from "./state/runDefaults.js";

const configuredApiBase = getStorageValue("apiBase");
const RECOMMENDATION_MUTED_KEY = STORAGE_KEYS.recommendationMuted;
const API_CANDIDATES = configuredApiBase
  ? [configuredApiBase]
  : ["http://127.0.0.1:8100", "http://127.0.0.1:8101", "http://127.0.0.1:8102"];
const apiClient = createApiClient(API_CANDIDATES);

const state = structuredClone(demoState);
let eventSource = null;
let replayTimer = null;
let seenEventIds = new Set();
let recommendationTimer = null;
let recommendationRenderDeferred = false;
let globalShortcutsBound = false;
let toastTimer = null;

function saveLayoutPreference() {
  persistLayoutPreference(state.layout);
}

function isTemporaryProjectPath(path) {
  const text = String(path || "").toLowerCase();
  return (
    text.includes("/pytest-") ||
    text.includes("\\pytest-") ||
    text.includes("/e2e_workspaces/") ||
    text.includes("\\e2e_workspaces\\") ||
    text.includes("/tmp/") ||
    text.includes("\\tmp\\") ||
    text.includes("/temp/") ||
    text.includes("\\temp\\")
  );
}

function visibleRecentProjects(limit = 6) {
  const projects = state.recentProjects || [];
  const clean = projects.filter((item) => !isTemporaryProjectPath(item.path));
  return clean.slice(0, limit);
}

function statusColor(status) {
  const colors = {
    created: "var(--slate)", planning: "var(--blue)", waiting_approval: "var(--amber)",
    running: "var(--green)", validating: "var(--blue)", cancelling: "var(--amber)",
    completed: "var(--green)", failed: "var(--coral)", cancelled: "var(--slate)",
    interrupted: "var(--coral)", recovering: "var(--amber)",
  };
  return colors[status] || "var(--slate)";
}

function getCapabilityOptions() {
  const groups = state.capabilityHub?.groups || [];
  return groups
    .flatMap((group) => group.items || [])
    .filter((item) => item.status !== "planned");
}

function capabilityDisplayName(capabilityId) {
  const capability = (state.capabilityHub?.capabilities || getCapabilityOptions()).find((item) => item.id === capabilityId);
  return capability?.name || capabilityId;
}

function capabilityTraceForEvent(event) {
  const trace = event.payload?.capability_trace;
  if (trace) {
    return {
      agent: trace.agent || event.agent || "Lead",
      capabilityName: trace.capability_name || trace.capabilityName || capabilityDisplayName(trace.capability_id),
      capabilityId: trace.capability_id || trace.capabilityId || "",
      kind: trace.kind || "tool",
      tool: trace.tool || event.payload?.tool || "",
    };
  }

  const tool = event.payload?.tool || String(event.title || "").match(/(?:工具调用|能力调用)：(.+)$/)?.[1];
  if (!tool) return null;
  const inferred = TOOL_CAPABILITY_TRACE[tool] || {
    capabilityName: "通用工具",
    capabilityId: "tool.generic",
    kind: "tool",
    agent: event.agent || "Lead",
  };
  return { ...inferred, tool };
}

function renderEventCapabilityTrace(event) {
  if (event.type !== "tool_call_finished" && event.type !== "capability_used") return "";
  const trace = capabilityTraceForEvent(event);
  if (!trace) return "";
  return `
    <div class="event-capability">
      <span>${escapeHtml(trace.agent)}</span>
      <strong>${escapeHtml(trace.capabilityName)}</strong>
      ${trace.tool ? `<em>${escapeHtml(trace.tool)}</em>` : ""}
    </div>
  `;
}

function inferTaskCapabilities(task) {
  const title = `${task?.title || ""} ${task?.description || ""}`.toLowerCase();
  const owner = String(task?.owner || "").toLowerCase();
  const capabilities = [];

  function add(item) {
    if (item && !capabilities.includes(item)) capabilities.push(item);
  }

  if (owner.includes("planner") || ["需求", "验收", "计划", "拆解", "文档", "接口"].some((keyword) => title.includes(keyword))) {
    add("tool.project_index");
  }
  if (owner.includes("coder") || ["实现", "代码", "界面", "本地存储", "文件", "样式"].some((keyword) => title.includes(keyword))) {
    add("tool.file_ops");
    add("tool.project_index");
  }
  if (["界面", "前端", "ui", "样式", "布局", "交互"].some((keyword) => title.includes(keyword))) {
    add("skill.frontend-polish");
  }
  if (owner.includes("tester") || ["测试", "验证", "质量", "报告", "复核"].some((keyword) => title.includes(keyword))) {
    add("skill.delivery-review");
    add("tool.recovery");
  }
  return capabilities.slice(0, 5);
}

function eventKind(type) {
  if (type === "tool_call_finished") return "tool";
  if (type === "capability_used") return "tool";
  if (type === "done") return "done";
  if (type === "error") return "error";
  return "message";
}

const TOOL_CAPABILITY_TRACE = {
  write_file: { capabilityName: "文件读写", capabilityId: "tool.file_ops", kind: "tool", agent: "Coder" },
  edit_file: { capabilityName: "文件读写", capabilityId: "tool.file_ops", kind: "tool", agent: "Coder" },
  read_file: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Coder" },
  list_directory: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  search_codebase: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  project_context: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  bash: { capabilityName: "交付复核 Skill", capabilityId: "skill.delivery-review", kind: "skill", agent: "Tester" },
  task_create: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  task_update: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Lead" },
  add_memory: { capabilityName: "偏好记忆", capabilityId: "tool.memory", kind: "tool", agent: "Lead" },
  recall_memories: { capabilityName: "偏好记忆", capabilityId: "tool.memory", kind: "tool", agent: "Planner" },
};

function render() {
  const focusedField = captureFocusedField();
  document.querySelector("#app").innerHTML = `
    <div class="app-shell">
      ${renderTopbar()}
      <main class="${layoutClass()}">
        ${renderSidebar()}
        ${renderChat()}
        ${renderRightPanel()}
        ${renderBottomPanel()}
      </main>
      ${renderToast()}
      ${renderCommandPalette()}
    </div>
  `;
  bindGlobalShortcutsOnce();
  bindEvents();
  restoreFocusedField(focusedField);
  resizePromptInput();
  scrollToLatestMessage();
}

function bindGlobalShortcutsOnce() {
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

function layoutClass() {
  const mode = currentLayoutMode();
  const classes = ["workspace", `layout-${mode}`];
  if (state.layout?.sidebarCollapsed) classes.push("sidebar-collapsed");
  if (state.layout?.rightCollapsed) classes.push("right-collapsed");
  if (state.layout?.bottomCollapsed) classes.push("bottom-collapsed");
  return classes.join(" ");
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

function persistLayoutMode(mode) {
  saveLayoutMode(mode);
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
  persistLayoutMode(nextMode);
  saveLayoutPreference();
  render();
}

function openCommandPalette(query = "") {
  const ui = ensureUiState();
  ui.commandPaletteOpen = true;
  ui.commandQuery = query;
  render();
  requestAnimationFrame(() => document.querySelector("#command-input")?.focus());
}

function closeCommandPalette() {
  const ui = ensureUiState();
  ui.commandPaletteOpen = false;
  ui.commandQuery = "";
  render();
}

function isActionBusy(action) {
  return Boolean(state.ui?.busyActions?.[action]);
}

function setActionBusy(action, busy) {
  ensureUiState();
  state.ui.busyActions = state.ui.busyActions || {};
  if (busy) {
    state.ui.busyActions[action] = true;
  } else {
    delete state.ui.busyActions[action];
  }
}

async function withBusyAction(action, callback) {
  if (isActionBusy(action)) return undefined;
  setActionBusy(action, true);
  render();
  try {
    return await callback();
  } catch (error) {
    showToast("error", "操作失败", error.message || String(error));
    return undefined;
  } finally {
    setActionBusy(action, false);
    render();
  }
}

function showToast(kind, title, content = "", duration = 2600) {
  ensureUiState();
  state.ui.toast = {
    kind,
    title,
    content,
    id: Date.now(),
  };
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    state.ui.toast = null;
    render();
  }, duration);
  render();
}

function renderToast() {
  return renderToastView(state.ui?.toast);
}

function renderCommandPalette() {
  return renderCommandPaletteView({
    ui: ensureUiState(),
    commands: filteredCommandItems(),
  });
}

function filteredCommandItems() {
  return filterCommandItems(ensureUiState().commandQuery);
}

async function executeCommand(commandId) {
  await executeCommandAction(commandId, {
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
  });
}

function renderTopbar() {
  return renderTopbarView({
    state,
    apiBase: apiClient.activeBase,
    isActionBusy,
    renderWorkspacePickerPopover,
  });
}

function renderWorkspacePickerPopover() {
  return renderWorkspacePickerPopoverView({
    state,
    recentProjects: visibleRecentProjects(4),
    isActionBusy,
  });
}

function renderSidebar() {
  return renderSidebarView({
    state,
    tabs: buildSidebarTabs(state),
    content: renderSidebarContent(),
  });
}

function renderSidebarContent() {
  return renderSidebarContentView(state);
}

function renderChat() {
  return renderChatView({ state, isActionBusy });
}

function renderRightPanel() {
  const tabs = buildRightPanelTabs({ state, ephemeralCount: ephemeralAgentPanelCount() });
  const activeRightTab = resolveRightTab(state.rightTab, tabs);
  if (state.rightTab !== activeRightTab) {
    state.rightTab = activeRightTab;
  }

  return renderRightPanelView({
    state,
    tabs,
    activeTab: activeRightTab,
    content: renderRightPanelContent(activeRightTab),
  });
}

function renderRightPanelContent(activeRightTab) {
  if (activeRightTab === "tasks") return renderTasks();
  if (activeRightTab === "team") return renderTeam();
  if (activeRightTab === "ephemeral") return renderEphemeralAgents();
  if (activeRightTab === "capabilities") return renderCapabilities();
  if (activeRightTab === "metrics") return renderMetrics();
  if (activeRightTab === "benchmarks") return renderBenchmarks();
  if (activeRightTab === "preferences") return renderPreferences();
  if (activeRightTab === "recovery") return renderRecovery();
  if (activeRightTab === "settings") return renderWorkspaceSettings();
  return "";
}

function ephemeralAgentPanelCount() {
  const panel = state.ephemeralAgents || {};
  return Number(panel.active_count || 0) + Number(panel.suggestions?.length || 0);
}

function renderTasks() {
  return renderTasksView({ state, inferTaskCapabilities, capabilityDisplayName });
}

function renderTeam() {
  return renderTeamView({ state, getCapabilityOptions, capabilityDisplayName });
}

function renderEphemeralAgents() {
  return renderEphemeralAgentsView({ state, blankEphemeralAgents, isEphemeralThreadReady, capabilityDisplayName });
}

function renderCapabilities() {
  return renderCapabilitiesView({ state, isActionBusy });
}

function renderMetrics() {
  return renderMetricsView(state);
}

function renderBenchmarks() {
  return renderBenchmarksView(state);
}

function renderPreferences() {
  return renderPreferencesView(state);
}

function renderWorkspaceSettings() {
  return renderWorkspaceSettingsView(state);
}

async function loadWorkspaceSettings() {
  state.workspaceSettings = await loadWorkspaceSettingsValue({ fetchJson });
}

async function saveWorkspaceSettings() {
  state.workspaceSettings = await saveWorkspaceSettingsValue({ requestJson, addTimelineEvent });
  render();
}

async function loadRecentProjects() {
  try {
    const result = await fetchJson("/api/workspace/recent");
    state.recentProjects = result.recent || [];
    renderRecentProjectsList();
  } catch {
    state.recentProjects = [];
  }
}

function renderRecentProjectsList() {
  const container = document.querySelector("#recent-projects-list");
  if (!container) return;
  const projects = visibleRecentProjects(6);
  container.innerHTML = renderRecentProjectsHtml(projects);
}

function renderBottomPanel() {
  const tabs = [
    ["report", "报告"],
    ["diff", "Diff"],
    ["timeline", "事件"],
    ["recovery", "恢复"],
    ["artifacts", "交付物"],
  ];
  return renderBottomPanelView({
    state,
    tabs,
    summary: renderBottomSummary(),
    content: renderBottomContent(),
  });
}

function renderBottomSummary() {
  const diffCount = state.diffFiles?.length || state.report.changedFiles?.length || 0;
  const score = state.artifactCenter?.summary?.score ?? "--";
  const coverage = Math.round((state.report.traceability?.coverageRate || state.artifactCenter?.summary?.coverage_rate || 0) * 100);
  const risks = state.recoveryCenter?.summary?.risk_count ?? state.report.risks?.length ?? 0;
  const items = [
    ["Diff", `${diffCount} 文件`],
    ["报告", `${score} 分`],
    ["覆盖", `${coverage}%`],
    ["风险", `${risks} 项`],
  ];

  return items
    .map(
      ([label, value]) => `
        <div class="summary-chip">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderBottomContent() {
  if (state.activeTab === "timeline") return renderTimeline();
  if (state.activeTab === "artifacts") return renderArtifacts();
  if (state.activeTab === "recovery") return renderRecovery();
  if (state.activeTab === "preview") return renderPreview();
  if (state.activeTab === "report") return renderReport();
  return renderDiffView();
}

function renderDiffView() {
  return renderDiffViewContent({ state, syncDiffFiles });
}

function renderArtifacts() {
  return renderArtifactsView(state.artifactCenter);
}

function renderRecovery() {
  return renderRecoveryView(state.recoveryCenter || {});
}

function renderTimeline() {
  return renderTimelineContent({ state, eventKind, renderEventCapabilityTrace });
}

function renderPreview() {
  return renderPreviewContent(state.previewUrl || "localhost:5173/demo-todo");
}

function renderReport() {
  return renderReportView(state);
}

function mapTraceability(traceability) {
  return {
    source: traceability.source || "generated",
    coverageRate: traceability.coverage_rate || 0,
    totalCount: traceability.total_count || 0,
    coveredCount: traceability.covered_count || 0,
    partialCount: traceability.partial_count || 0,
    missingCount: traceability.missing_count || 0,
    requirements: traceability.requirements || [],
  };
}

function setTraceability(traceability) {
  state.report.traceability = mapTraceability(traceability);
  state.report.requirements = state.report.traceability.requirements.map(
    (item) => `${item.id}: ${item.title}`,
  );
}

function bindEvents() {
  bindDomEvents({
    state,
    recommendationMutedKey: RECOMMENDATION_MUTED_KEY,
    getRecommendationRenderDeferred: () => recommendationRenderDeferred,
    setRecommendationRenderDeferred: (value) => {
      recommendationRenderDeferred = value;
    },
    openCommandPalette,
    closeCommandPalette,
    ensureUiState,
    render,
    filteredCommandItems,
    executeCommand,
    saveLayoutPreference,
    saveWorkspaceSettings,
    withBusyAction,
    openWorkspace,
    startNewSession,
    showToast,
    shortPath,
    restoreRun,
    startReplay,
    pauseReplay,
    resetReplayToStart,
    setReplaySpeed,
    submitApprovalDecision,
    refreshWorkspaceData,
    loadWorkspaceOverview,
    addTimelineEvent,
    buildReportText,
    validateMcpConfig,
    loadMcpTools,
    installMcpPreset,
    saveMcpServerConfig,
    loadSkillDetail,
    saveSkillContent,
    cancelSkillEdit,
    deleteSkill,
    requestJson,
    loadRecoveryCenter,
    scheduleCapabilityRecommendation,
    resizePromptInput,
    runPrompt,
    createCustomAgent,
    refreshConversationTeam,
    removeTeamMember,
    importCustomSkill,
    runBenchmark,
    createPreference,
    blankEphemeralAgents,
    suggestEphemeralAgents,
    refreshEphemeralAgents,
    spawnEphemeralAgent,
    completeEphemeralAgent,
    archiveEphemeralAgent,
  });
}
function scrollToLatestMessage() {
  const list = document.querySelector("#message-list");
  if (list) {
    list.scrollTop = list.scrollHeight;
  }
}

function resizePromptInput(input = document.querySelector("#prompt-input")) {
  if (!input) return;
  input.style.height = "auto";
  const nextHeight = Math.min(Math.max(input.scrollHeight, 48), 144);
  input.style.height = `${nextHeight}px`;
}

function addTimelineEvent(event) {
  state.events.push({
    time: nowTime(),
    ...event,
  });
  render();
}

function addMessage(message) {
  state.messages.push({
    time: nowTime(),
    ...message,
  });
}







function blankApproval() {
  return blankApprovalValue(demoState);
}



function isEphemeralThreadReady() {
  const threadId = String(state.currentThreadId || "");
  return Boolean(threadId && threadId !== "pending");
}

function upsertEphemeralAgent(agent, { removeIfHidden = true } = {}) {
  if (!agent?.agent_id) return;
  const panel = state.ephemeralAgents || blankEphemeralAgents();
  const agents = Array.isArray(panel.agents) ? [...panel.agents] : [];
  const index = agents.findIndex((item) => item.agent_id === agent.agent_id);
  const shouldHide = removeIfHidden && !panel.includeArchived && ["archived", "expired"].includes(agent.status);
  if (shouldHide) {
    const wasVisible = index >= 0 && !["archived", "expired"].includes(agents[index]?.status);
    state.ephemeralAgents = normalizeEphemeralAgentsResult({
      ...panel,
      agents: agents.filter((item) => item.agent_id !== agent.agent_id),
      archived_count: Number(panel.archived_count || 0) + (wasVisible ? 1 : 0),
    }, panel);
    return;
  }
  if (index >= 0) {
    agents[index] = { ...agents[index], ...agent };
  } else {
    agents.unshift(agent);
  }
  const activeCount = agents.filter((item) => !["archived", "expired"].includes(item.status)).length;
  state.ephemeralAgents = normalizeEphemeralAgentsResult({
    ...panel,
    agents,
    active_count: activeCount,
    total: agents.length,
  }, panel);
}

function setDiffState(diff, changedFiles = []) {
  state.diff = diff || "";
  state.diffFiles = parseUnifiedDiff(state.diff, changedFiles);
  if (!state.diffFiles.some((file) => file.path === state.selectedDiffFile)) {
    state.selectedDiffFile = state.diffFiles[0]?.path || "";
  }
}

function syncDiffFiles() {
  if (state.diffFiles?.length) {
    if (!state.selectedDiffFile || !state.diffFiles.some((file) => file.path === state.selectedDiffFile)) {
      state.selectedDiffFile = state.diffFiles[0]?.path || "";
    }
    return;
  }
  setDiffState(state.diff, state.report.changedFiles);
}

function resetRunView(prompt = "") {
  seenEventIds = new Set();
  state.messages = [];
  state.events = [];
  state.tasks = [];
  state.files = [];
  state.metrics = {
    tasks: 0,
    files: 0,
    toolCalls: 0,
    tokens: "--",
    tests: "--",
  };
  setDiffState("", []);
  state.previewUrl = "";
  state.report = blankReport();
  state.artifactCenter = blankArtifactCenter("loading");
  state.recoveryCenter = blankRecoveryCenter("loading");
  state.approval = blankApproval();
  state.ephemeralAgents = blankEphemeralAgents();
  state.showCompletedTasks = false;
  if (prompt) {
    state.messages.push({
      role: "user",
      author: "用户",
      time: "",
      content: prompt,
    });
  }
}

function stopReplayTimer() {
  if (replayTimer) {
    window.clearTimeout(replayTimer);
    replayTimer = null;
  }
}

function setReplayEvents(events = [], { prompt = state.prompt, startedAt = "" } = {}) {
  state.replay.events = events;
  state.replay.index = events.length;
  state.replay.status = events.length ? "ready" : "idle";
  state.replay.prompt = prompt || "";
  state.replay.startedAt = startedAt || "";
}

function clearReplayState() {
  stopReplayTimer();
  state.replay = structuredClone(demoState.replay);
}

function applyRunStateSnapshot(events = []) {
  const stateEventTypes = new Set([
    "approval_resolved",
    "tool_approval_required",
    "run_waiting_approval",
    "stage_updated",
    "task_created",
    "task_updated",
    "team_updated",
    "tool_call_finished",
    "file_changed",
    "diff_updated",
    "test_finished",
    "preview_started",
    "report_ready",
    "traceability_ready",
    "done",
    "error",
  ]);
  events
    .filter((event) => stateEventTypes.has(event.type))
    .forEach((event) => {
      handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false, focusPanel: false });
    });
}

async function refreshReplayEvents(threadId, { prompt = state.prompt, startedAt = "" } = {}) {
  if (!threadId || threadId === "pending") return;
  try {
    const result = await fetchJson(`/api/runs/${encodeURIComponent(threadId)}/events/history`);
    const events = result.events || [];
    setReplayEvents(events, { prompt, startedAt });
    applyRunStateSnapshot(events);
    render();
  } catch {
    // Replay is an enhancement; the live run view remains usable without it.
  }
}

function resetReplayToStart() {
  if (!state.replay.events.length) return;
  stopReplayTimer();
  state.replay.index = 0;
  state.replay.status = "paused";
  state.status = "replay_paused";
  state.activeTab = "timeline";
  resetRunView(state.replay.prompt || state.prompt);
  if (state.messages[0] && state.replay.startedAt) {
    state.messages[0].time = state.replay.startedAt;
  }
  render();
}

function setReplaySpeed(speed) {
  state.replay.speed = speed;
  if (state.replay.status === "playing") {
    stopReplayTimer();
    scheduleReplayStep();
  } else {
    render();
  }
}

function startReplay() {
  if (!state.replay.events.length) return;
  if (state.replay.index >= state.replay.events.length) {
    resetReplayToStart();
  }
  state.replay.status = "playing";
  state.status = "replaying";
  state.activeTab = "timeline";
  render();
  scheduleReplayStep();
}

function pauseReplay() {
  stopReplayTimer();
  if (state.replay.events.length) {
    state.replay.status = "paused";
    state.status = "replay_paused";
  }
  render();
}

function scheduleReplayStep() {
  stopReplayTimer();
  if (state.replay.status !== "playing") return;
  const delay = Math.max(120, 900 / Number(state.replay.speed || 1));
  replayTimer = window.setTimeout(() => {
    replayTimer = null;
    applyReplayStep();
  }, delay);
}

async function finishReplay() {
  stopReplayTimer();
  state.replay.status = "finished";
  state.replay.index = state.replay.events.length;
  if (state.status === "replaying") {
    state.status = "completed";
  }
  await hydrateRunArtifacts(state.currentThreadId, { refreshWorkspace: false });
  render();
}

function applyReplayStep() {
  if (state.replay.status !== "playing") return;
  const event = state.replay.events[state.replay.index];
  if (!event) {
    finishReplay();
    return;
  }

  handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false });
  state.replay.index += 1;

  if (state.replay.index >= state.replay.events.length) {
    finishReplay();
    return;
  }

  render();
  scheduleReplayStep();
}

async function requestJson(path, options = {}) {
  return apiClient.requestJson(path, options);
}

async function fetchJson(path) {
  return apiClient.fetchJson(path);
}



async function submitApprovalDecision(decision) {
  await submitApprovalDecisionAction(decision, {
    state,
    requestJson,
    render,
    handleAgentEvent,
    refreshReplayEvents,
    addTimelineEvent,
    approvalDecisionLabel,
  });
}

function mapRunHistoryItem(run) {
  return mapRunHistoryItemValue(run, { runTitle, formatTime });
}

function mapConversationItem(conversation) {
  return mapConversationItemValue(conversation, { runTitle, formatTime });
}

function conversationQuery() {
  return state.workspaceDir ? `?workspace_dir=${encodeURIComponent(state.workspaceDir)}` : "";
}

function teamToBackendMembers(team = state.team) {
  return team.map((member) => ({
    name: member.name,
    role: member.role,
    goal: member.goal || "",
    tools: Array.isArray(member.tools) ? member.tools : [],
    capabilities: Array.isArray(member.capabilities) ? member.capabilities : [],
    artifacts: Array.isArray(member.artifacts) ? member.artifacts : [],
  }));
}

function applyConversation(conversation, { reset = false } = {}) {
  if (!conversation?.conversation_id) return;
  state.currentConversationId = conversation.conversation_id;
  state.currentThreadId = conversation.current_thread_id || conversation.conversation_id;
  state.status = conversation.status === "draft" ? "idle" : conversation.status || "idle";
  state.prompt = conversation.prompt || state.prompt || "";
  if (conversation.team?.members?.length) {
    state.team = mapBackendTeam(conversation.team.members);
  }
  upsertRun(mapConversationItem(conversation));
  if (reset) {
    resetRunView("");
    state.messages = [
      {
        role: "assistant",
        author: "Lead Agent",
        time: nowTime(),
        content: state.workspaceDir
          ? "新会话已创建。输入需求后，我会直接在当前项目中启动一次可追踪运行。"
          : "新会话已创建。建议先打开一个项目目录，再开始交付任务。",
      },
    ];
  }
}

function upsertRun(run) {
  if (!run?.id) return;
  const index = state.runs.findIndex((item) => item.id === run.id);
  if (index >= 0) {
    state.runs[index] = { ...state.runs[index], ...run };
  } else {
    state.runs.unshift(run);
  }
}

async function loadRunHistory() {
  try {
    const [runsResult, conversationsResult] = await Promise.allSettled([
      fetchJson("/api/runs?limit=50"),
      fetchJson(`/api/conversations?limit=50${state.workspaceDir ? `&workspace_dir=${encodeURIComponent(state.workspaceDir)}` : ""}`),
    ]);
    const runs = runsResult.status === "fulfilled" ? (runsResult.value.runs || []).map(mapRunHistoryItem) : [];
    const conversations =
      conversationsResult.status === "fulfilled"
        ? (conversationsResult.value.conversations || []).map(mapConversationItem)
        : [];
    state.conversations = conversations;
    const runIdsLinkedToConversations = new Set(conversations.flatMap((item) => item.runIds || []));
    const standaloneRuns = runs.filter((run) => !runIdsLinkedToConversations.has(run.id));
    const transientRuns = state.runs.filter(
      (run) =>
        (run.localOnly || run.status === "running") &&
        !standaloneRuns.some((item) => item.id === run.id) &&
        !conversations.some((item) => item.id === run.id),
    );
    state.runs = [...transientRuns, ...conversations, ...standaloneRuns];
  } catch {
    // Keep the bundled demo sessions when the backend is not available.
  }
  render();
}

async function loadWorkspaceState() {
  try {
    const result = await fetchJson("/api/workspaces");
    const current = result.current_workspace || "";
    state.workspaceMeta = {
      default_workspace: result.default_workspace || "",
      workspace_root: result.workspace_root || "",
      project_root: result.project_root || "",
      is_default_workspace: Boolean(result.is_default_workspace),
    };
    if (current) {
      state.workspaceDir = current;
      state.workspaceInput = current;
      localStorage.setItem(STORAGE_KEYS.workspaceDir, current);
    }
  } catch {
    // Keep the previous workspace path when the backend is not reachable.
  }
}

async function loadWorkspaceOverview() {
  try {
    const query = state.workspaceDir ? `?workspace_dir=${encodeURIComponent(state.workspaceDir)}` : "";
    const overview = await fetchJson(`/api/workspace/overview${query}`);
    state.projectOverview = {
      ...state.projectOverview,
      ...overview,
      summary: { ...(state.projectOverview?.summary || {}), ...(overview.summary || {}) },
      project_index: { ...(state.projectOverview?.project_index || {}), ...(overview.project_index || {}) },
      recovery: { ...(state.projectOverview?.recovery || {}), ...(overview.recovery || {}) },
    };
  } catch {
    state.projectOverview = {
      ...state.projectOverview,
      workspace_dir: state.workspaceDir || state.projectOverview?.workspace_dir || "",
    };
  }
}

async function openWorkspace() {
  const dir = (state.workspaceInput || "").trim();
  if (!dir) {
    addTimelineEvent({
      type: "error",
      title: "工作区路径为空",
      content: "请输入项目目录的绝对路径。",
    });
    return;
  }

  try {
    const result = await requestJson("/api/workspaces/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: dir }),
    });
    state.workspaceDir = result.path || dir;
    state.workspaceInput = state.workspaceDir;
    state.ui.workspacePickerOpen = false;
    localStorage.setItem(STORAGE_KEYS.workspaceDir, state.workspaceDir);
    await startNewSession({ announce: false });
    addTimelineEvent({
      type: "workspace_opened",
      title: "项目目录已打开",
      content: state.workspaceDir,
    });
    showToast("success", "项目目录已打开", shortPath(state.workspaceDir));
    await Promise.allSettled([
      loadWorkspaceOverview(),
      loadRunHistory(),
      loadCapabilities(),
      loadBenchmarks(),
      loadMemoryProfile(),
      loadRecoveryCenter(),
      loadWorkspaceSettings(),
      loadRecentProjects(),
      refreshWorkspaceData({ allowEmpty: true }),
    ]);
    render();
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "打开项目目录失败",
      content: error.message,
    });
    showToast("error", "打开失败", error.message);
    render();
  }
}

async function startNewSession({ draftId = "", keepRunItem = false, announce = true } = {}) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  stopReplayTimer();
  clearReplayState();

  let id = draftId || `draft-${Date.now().toString(36)}`;
  let conversation = null;
  if (!draftId) {
    try {
      const result = await requestJson("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: "", workspace_dir: state.workspaceDir || undefined }),
      });
      conversation = result.conversation;
      id = conversation?.conversation_id || id;
    } catch (error) {
      addTimelineEvent({
        type: "error",
        title: "会话后端暂不可用",
        content: `已创建本地草稿：${error.message}`,
      });
    }
  }
  state.currentConversationId = conversation?.conversation_id || (id.startsWith("conv-") ? id : "");
  state.currentThreadId = id;
  state.status = "idle";
  state.prompt = "";
  state.activeTab = "timeline";
  resetRunView("");
  state.messages = [
    {
      role: "assistant",
      author: "Lead Agent",
      time: nowTime(),
      content: state.workspaceDir
        ? "新会话已创建。输入需求后，我会直接启动运行，并在右侧展示任务、团队和临时 Agent。"
        : "新会话已创建。建议先打开一个项目目录，再开始交付任务。",
    },
  ];

  if (conversation) {
    applyConversation(conversation, { reset: false });
  }

  if (!keepRunItem) {
    state.runs = state.runs.filter((run) => !run.localOnly);
    upsertRun({
      id,
      kind: conversation ? "conversation" : "run",
      conversationId: conversation?.conversation_id || "",
      title: conversation?.title || "新会话",
      status: "idle",
      time: nowTime(),
      prompt: "",
      localOnly: !conversation,
      agentCount: conversation?.team?.members?.length || state.team.length,
    });
  }

  if (announce) {
    render();
    refreshWorkspaceData({ allowEmpty: true }).catch(() => render());
  }
}

async function restoreRun(threadId) {
  if (!threadId) return;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  stopReplayTimer();

  const selectedRun = state.runs.find((run) => run.id === threadId);
  if (selectedRun?.kind === "conversation") {
    await restoreConversation(selectedRun.conversationId || selectedRun.id);
    return;
  }
  if (threadId === state.currentThreadId) return;
  if (selectedRun?.localOnly) {
    await startNewSession({ draftId: selectedRun.id, keepRunItem: true });
    return;
  }
  state.currentThreadId = threadId;
  state.status = selectedRun?.status || "running";
  state.prompt = selectedRun?.prompt || "";
  state.activeTab = "timeline";
  resetRunView(state.prompt);
  addTimelineEvent({
    type: "run_restoring",
    title: "正在恢复历史运行",
    content: selectedRun?.title || threadId,
  });

  try {
    const [sessionResult, eventsResult] = await Promise.allSettled([
      fetchJson(`/api/runs/${encodeURIComponent(threadId)}`),
      fetchJson(`/api/runs/${encodeURIComponent(threadId)}/events/history`),
    ]);

    const session = sessionResult.status === "fulfilled" ? sessionResult.value : null;
    const prompt = session?.prompt || selectedRun?.prompt || "";
    if (session?.conversation_id) {
      state.currentConversationId = session.conversation_id;
    }
    if (Array.isArray(session?.team) && session.team.length) {
      state.team = mapBackendTeam(session.team);
    }
    state.prompt = prompt || state.prompt;
    state.status = session?.status || selectedRun?.status || state.status;

    resetRunView(state.prompt);
    if (session?.execution_plan) {
      syncTasksFromExecutionPlan(session.execution_plan);
      if (state.status === "running" && state.tasks.length) {
        state.rightTab = "tasks";
      }
    }
    if (state.prompt) {
      state.messages[0].time = formatTime(session?.created_at) || selectedRun?.time || "";
    }

    upsertRun({
      id: threadId,
      title: runTitle(state.prompt, threadId),
      status: state.status,
      time: formatTime(session?.updated_at || session?.created_at) || selectedRun?.time || "",
      mode: session?.mode || selectedRun?.mode || "agenthub_delivery",
      prompt: state.prompt,
      eventCount: selectedRun?.eventCount || 0,
      changedFilesCount: selectedRun?.changedFilesCount || 0,
      hasDiff: selectedRun?.hasDiff || false,
      hasReport: selectedRun?.hasReport || false,
      lastEventType: selectedRun?.lastEventType || "",
    });

    if (eventsResult.status === "fulfilled") {
      const events = eventsResult.value.events || [];
      setReplayEvents(events, {
        prompt: state.prompt,
        startedAt: formatTime(session?.created_at) || selectedRun?.time || "",
      });
      events.forEach((event) => {
        handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false, focusPanel: false });
      });
      state.replay.index = events.length;
      state.replay.status = events.length ? "ready" : "idle";
      const currentRun = state.runs.find((run) => run.id === threadId);
      if (currentRun) {
        currentRun.eventCount = events.length;
        currentRun.lastEventType = events.at(-1)?.type || currentRun.lastEventType;
      }
    } else {
      state.events.push({
        type: "error",
        title: "历史事件读取失败",
        content: eventsResult.reason?.message || "后端未返回事件列表。",
        time: nowTime(),
      });
    }

    await hydrateRunArtifacts(threadId, { refreshWorkspace: false });
    if (state.report.markdown) {
      state.activeTab = "report";
    } else if (state.diff) {
      state.activeTab = "diff";
    }
  } catch (error) {
    state.status = "failed";
    updateCurrentRunStatus("failed");
    state.events.push({
      type: "error",
      title: "恢复历史运行失败",
      content: error.message,
      time: nowTime(),
    });
  }

  render();
}

async function restoreConversation(conversationId) {
  if (!conversationId || conversationId === state.currentConversationId) return;
  state.activeTab = "timeline";
  resetRunView("");
  addTimelineEvent({
    type: "conversation_restoring",
    title: "正在恢复会话",
    content: conversationId,
  });

  try {
    const result = await fetchJson(`/api/conversations/${encodeURIComponent(conversationId)}${conversationQuery()}`);
    const conversation = result.conversation;
    applyConversation(conversation, { reset: false });
    state.messages = [
      {
        role: "assistant",
        author: "Lead Agent",
        time: formatTime(conversation.updated_at) || nowTime(),
        content: conversation.current_thread_id
          ? "会话已恢复。可以查看上次运行，也可以输入新需求继续让 nanoCursor 组队执行。"
          : "会话已恢复。输入需求后，我会基于这个会话团队直接启动运行。",
      },
    ];
    if (conversation.current_thread_id) {
      await restoreRun(conversation.current_thread_id);
      state.currentConversationId = conversationId;
    }
  } catch (error) {
    state.status = "failed";
    addTimelineEvent({
      type: "error",
      title: "恢复会话失败",
      content: error.message,
    });
  }

  render();
}

async function createCustomAgent() {
  const name = document.querySelector("#agent-name")?.value.trim();
  const role = document.querySelector("#agent-role")?.value.trim();
  const goal = document.querySelector("#agent-goal")?.value.trim() || "";
  const tools = (document.querySelector("#agent-tools")?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const capabilities = Array.from(document.querySelectorAll("input[name='agent-capability']:checked"))
    .map((item) => item.value)
    .filter(Boolean);

  if (!name || !role) {
    addTimelineEvent({
      type: "error",
      title: "Agent 信息不完整",
      content: "请填写 Agent 名称和角色。",
    });
    return;
  }

  try {
    if (state.currentConversationId) {
      const nextTeam = [
        ...teamToBackendMembers(),
        {
          name,
          role,
          goal,
          tools,
          capabilities,
        },
      ];
      await saveConversationTeam(nextTeam);
      state.rightTab = "team";
      addTimelineEvent({
        type: "team_updated",
        title: "会话 Agent 已添加",
        content: `${name} 将以 ${role} 角色参与当前会话，已装配 ${capabilities.length} 个能力包。`,
      });
      return;
    }

    const result = await requestJson("/api/team/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, role, goal, tools, capabilities }),
    });
    state.team = mapBackendTeam(result.members || []);
    state.rightTab = "team";
    addTimelineEvent({
      type: "team_updated",
      title: "自定义 Agent 已添加",
      content: `${name} 将以 ${role} 角色参与后续交付，已装配 ${capabilities.length} 个能力包。`,
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "添加 Agent 失败",
      content: error.message,
    });
  }
}

async function saveConversationTeam(members) {
  await ensureConversation(state.prompt);
  const result = await requestJson(`/api/conversations/${encodeURIComponent(state.currentConversationId)}/team`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ members, workspace_dir: state.workspaceDir || undefined }),
  });
  state.team = mapBackendTeam(result.team?.members || members);
  upsertRun({
    id: state.currentConversationId,
    kind: "conversation",
    conversationId: state.currentConversationId,
    title: runTitle(state.prompt, "新会话"),
    status: state.status === "running" ? "running" : "idle",
    time: nowTime(),
    prompt: state.prompt,
    agentCount: state.team.length,
  });
}

async function removeTeamMember(index) {
  if (index < 0 || index >= state.team.length || state.team.length <= 1) return;
  const removed = state.team[index];
  const members = teamToBackendMembers(state.team.filter((_, itemIndex) => itemIndex !== index));
  try {
    await saveConversationTeam(members);
    addTimelineEvent({
      type: "team_updated",
      title: "会话 Agent 已移除",
      content: `${removed.name} 已从当前会话团队中移除。`,
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "移除 Agent 失败",
      content: error.message,
    });
  }
}

async function refreshEphemeralAgents({ includeArchived = false, renderAfter = true } = {}) {
  if (!isEphemeralThreadReady()) return;
  const previous = state.ephemeralAgents || blankEphemeralAgents();
  try {
    const query = includeArchived ? "?include_archived=true" : "";
    const result = await fetchJson(`/api/runs/${encodeURIComponent(state.currentThreadId)}/agents${query}`);
    state.ephemeralAgents = normalizeEphemeralAgentsResult(
      {
        ...result,
        includeArchived,
        suggestions: previous.suggestions || [],
      },
      previous,
    );
  } catch (error) {
    state.ephemeralAgents = {
      ...previous,
      status: "error",
      error: error.message,
    };
  }
  if (renderAfter) render();
}

async function suggestEphemeralAgents() {
  if (!isEphemeralThreadReady()) return;
  const previous = state.ephemeralAgents || blankEphemeralAgents();
  state.ephemeralAgents = {
    ...previous,
    status: "loading",
    error: "",
  };
  state.rightTab = "ephemeral";
  render();

  try {
    const result = await requestJson(`/api/runs/${encodeURIComponent(state.currentThreadId)}/agents/suggest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: state.prompt || "",
        max_agents: 4,
        mcp_plan: [],
      }),
    });
    state.ephemeralAgents = normalizeEphemeralAgentsResult(
      {
        ...previous,
        ...result,
        agents: previous.agents || [],
        includeArchived: previous.includeArchived,
      },
      previous,
    );
    addTimelineEvent({
      type: "ephemeral_agents_suggested",
      title: "临时子 Agent 建议已生成",
      content: `Lead 推荐 ${result.suggestions?.length || 0} 个任务级子 Agent。`,
    });
  } catch (error) {
    state.ephemeralAgents = {
      ...previous,
      status: "error",
      error: error.message,
    };
    addTimelineEvent({
      type: "error",
      title: "临时子 Agent 建议失败",
      content: error.message,
    });
  }
}

async function spawnEphemeralAgent(index) {
  const panel = state.ephemeralAgents || blankEphemeralAgents();
  const suggestion = panel.suggestions?.[index];
  if (!suggestion || !isEphemeralThreadReady()) return;
  try {
    const result = await requestJson(`/api/runs/${encodeURIComponent(state.currentThreadId)}/agents/spawn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent: suggestion }),
    });
    const nextSuggestions = panel.suggestions.filter((_, itemIndex) => itemIndex !== index);
    state.ephemeralAgents = normalizeEphemeralAgentsResult(
      {
        ...panel,
        suggestions: nextSuggestions,
      },
      panel,
    );
    upsertEphemeralAgent(result.agent, { removeIfHidden: false });
    state.rightTab = "ephemeral";
    addTimelineEvent({
      type: "ephemeral_agent_spawned",
      title: "临时子 Agent 已加入",
      content: `${result.agent?.name || suggestion.name} 将处理本轮任务的独立子问题。`,
      payload: result.agent || suggestion,
    });
    await refreshEphemeralAgents({ includeArchived: panel.includeArchived, renderAfter: false });
  } catch (error) {
    state.ephemeralAgents = {
      ...panel,
      status: "error",
      error: error.message,
    };
    addTimelineEvent({
      type: "error",
      title: "临时子 Agent 加入失败",
      content: error.message,
    });
  }
  render();
}

async function completeEphemeralAgent(agentId) {
  if (!agentId || !isEphemeralThreadReady()) return;
  const panel = state.ephemeralAgents || blankEphemeralAgents();
  const agent = panel.agents?.find((item) => item.agent_id === agentId);
  const summary = window.prompt(
    `填写 ${agent?.name || "临时子 Agent"} 的完成摘要`,
    `${agent?.name || "临时子 Agent"} 已完成本轮子任务。`,
  );
  if (summary === null) return;

  try {
    const result = await requestJson(
      `/api/runs/${encodeURIComponent(state.currentThreadId)}/agents/${encodeURIComponent(agentId)}/complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          summary,
          evidence: [],
          risks: [],
          artifacts: [],
          recommended_next_actions: ["交给 Lead 汇总到交付报告。"],
        }),
      },
    );
    upsertEphemeralAgent(result.agent);
    await refreshEphemeralAgents({ includeArchived: panel.includeArchived, renderAfter: false });
    addTimelineEvent({
      type: "ephemeral_agent_completed",
      title: "临时子 Agent 已完成",
      content: summary,
      payload: result.agent,
    });
  } catch (error) {
    state.ephemeralAgents = {
      ...panel,
      status: "error",
      error: error.message,
    };
    addTimelineEvent({
      type: "error",
      title: "临时子 Agent 完成失败",
      content: error.message,
    });
  }
  render();
}

async function archiveEphemeralAgent(agentId) {
  if (!agentId || !isEphemeralThreadReady()) return;
  const panel = state.ephemeralAgents || blankEphemeralAgents();
  const agent = panel.agents?.find((item) => item.agent_id === agentId);
  const reason = window.prompt(
    `归档 ${agent?.name || "临时子 Agent"} 的原因`,
    "本轮任务不再需要该临时子 Agent。",
  );
  if (reason === null) return;

  try {
    const result = await requestJson(
      `/api/runs/${encodeURIComponent(state.currentThreadId)}/agents/${encodeURIComponent(agentId)}/archive`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      },
    );
    upsertEphemeralAgent(result.agent);
    await refreshEphemeralAgents({ includeArchived: panel.includeArchived, renderAfter: false });
    addTimelineEvent({
      type: "ephemeral_agent_archived",
      title: "临时子 Agent 已归档",
      content: reason,
      payload: result.agent,
    });
  } catch (error) {
    state.ephemeralAgents = {
      ...panel,
      status: "error",
      error: error.message,
    };
    addTimelineEvent({
      type: "error",
      title: "临时子 Agent 归档失败",
      content: error.message,
    });
  }
  render();
}

async function loadBenchmarks() {
  try {
    const result = await fetchJson("/api/benchmarks");
    if (Array.isArray(result.benchmarks) && result.benchmarks.length) {
      state.benchmarks = result.benchmarks;
      render();
    }
  } catch {
    render();
  }
}

async function importCustomSkill() {
  const name = document.querySelector("#skill-name-input")?.value.trim();
  const content = document.querySelector("#skill-content-input")?.value.trim() || "";

  if (!name) {
    addTimelineEvent({
      type: "error",
      title: "Skill 名称为空",
      content: "请先填写自定义 Skill 名称。",
    });
    return;
  }

  try {
    const result = await requestJson("/api/capabilities/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content, description: content.slice(0, 180) }),
    });
    state.capabilityHub = result.hub || state.capabilityHub;
    state.rightTab = "capabilities";
    addTimelineEvent({
      type: "capability_used",
      title: "自定义 Skill 已导入",
      content: `${name} 已写入当前项目的 .nanocursor/skills。`,
      payload: {
        capability_trace: {
          capability_name: name,
          capability_id: result.skill?.id || `skill.${name}`,
          kind: "skill",
          agent: "Lead",
        },
      },
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "导入 Skill 失败",
      content: error.message,
    });
  }
}

async function loadCapabilities() {
  try {
    const result = await fetchJson("/api/capabilities");
    if (Array.isArray(result.groups)) {
      state.capabilityHub = result;
      state.capabilityRecommendation = inferLocalCapabilityRecommendation(state.prompt);
      render();
    }
  } catch {
    state.capabilityRecommendation = inferLocalCapabilityRecommendation(state.prompt);
    render();
  }
}

async function loadMcpConfig() {
  try {
    const [config, status, presets] = await Promise.all([
      fetchJson("/api/capabilities/mcp"),
      fetchJson("/api/capabilities/mcp/status").catch(() => ({ servers: {} })),
      fetchJson("/api/capabilities/mcp/presets").catch(() => ({ presets: [] })),
    ]);
    state.mcpConfig = normalizeMcpConfig(config, status, presets);
  } catch {
    state.mcpConfig = normalizeMcpConfig(state.mcpConfig || {});
  }
  render();
}

function normalizeMcpConfig(raw = {}, status = null, presetsPayload = null) {
  return normalizeMcpConfigPayload(raw, {
    status,
    presetsPayload,
    previous: state.mcpConfig || {},
  });
}

async function installMcpPreset(presetId) {
  if (!presetId) return;
  await withBusyAction(`install-mcp-preset:${presetId}`, async () => {
    try {
      const result = await requestJson(`/api/capabilities/mcp/presets/${encodeURIComponent(presetId)}/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      await loadMcpConfig();
      await loadCapabilities();
      await loadWorkspaceOverview();
      addTimelineEvent({
        type: "capability_used",
        title: "MCP 预设已启用",
        content: `${result.preset?.name || presetId} 已写入当前项目配置。`,
      });
      showToast("success", "MCP 预设已启用");
    } catch (error) {
      addTimelineEvent({
        type: "error",
        title: "启用 MCP 预设失败",
        content: error.message,
      });
      showToast("error", "启用 MCP 预设失败");
    }
  });
}

async function validateMcpConfig(serverId) {
  try {
    const result = await requestJson("/api/capabilities/mcp/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_id: serverId || null }),
    });
    state.mcpConfig = state.mcpConfig || {};
    state.mcpConfig.validationByServer = {
      ...(state.mcpConfig.validationByServer || {}),
      [serverId || "all"]: result.checks || [],
    };
  } catch {
    state.mcpConfig = state.mcpConfig || {};
    state.mcpConfig.validationByServer = {
      ...(state.mcpConfig.validationByServer || {}),
      [serverId || "all"]: [],
    };
  }
  render();
}

async function loadMcpTools(serverId, refresh = true) {
  if (!serverId) return;
  state.mcpConfig = normalizeMcpConfig(state.mcpConfig || {});
  state.mcpConfig.toolsByServer = {
    ...(state.mcpConfig.toolsByServer || {}),
    [serverId]: {
      ...(state.mcpConfig.toolsByServer?.[serverId] || {}),
      loading: true,
      error: "",
    },
  };
  render();

  try {
    const result = await fetchJson(`/api/capabilities/mcp/${encodeURIComponent(serverId)}/tools${refresh ? "?refresh=true" : ""}`);
    state.mcpConfig.toolsByServer = {
      ...(state.mcpConfig.toolsByServer || {}),
      [serverId]: result,
    };
    const status = await fetchJson(`/api/capabilities/mcp/${encodeURIComponent(serverId)}/status`).catch(() => null);
    if (status) {
      state.mcpConfig.statusByServer = {
        ...(state.mcpConfig.statusByServer || {}),
        [serverId]: status,
      };
    }
    if (result.ok) {
      addTimelineEvent({
        type: "capability_used",
        title: "MCP 工具已刷新",
        content: `${serverId} 暴露 ${result.tools?.length || 0} 个工具。`,
      });
    }
  } catch (error) {
    state.mcpConfig.toolsByServer = {
      ...(state.mcpConfig.toolsByServer || {}),
      [serverId]: {
        ok: false,
        tools: [],
        error: error.message,
      },
    };
    addTimelineEvent({
      type: "error",
      title: "刷新 MCP 工具失败",
      content: error.message,
    });
  }
  render();
}

async function saveMcpServerConfig() {
  const serverId = document.querySelector("#mcp-server-name-input")?.value.trim();
  const command = document.querySelector("#mcp-command-input")?.value.trim();
  const args = parseMcpArgs(document.querySelector("#mcp-args-input")?.value || "");
  const envKeys = parseMcpEnvKeys(document.querySelector("#mcp-env-input")?.value || "");

  if (!serverId || !command) {
    addTimelineEvent({
      type: "error",
      title: "MCP 配置不完整",
      content: "请填写 server 名称和启动命令。",
    });
    return;
  }

  try {
    const result = await requestJson("/api/capabilities/mcp/servers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_id: serverId, command, args, env_keys: envKeys }),
    });
    await loadMcpConfig();
    await loadCapabilities();
    await loadWorkspaceOverview();
    addTimelineEvent({
      type: "capability_used",
      title: "MCP Server 已配置",
      content: `${result.server?.id || serverId} 已写入 .nanocursor/mcp.json。`,
      payload: {
        capability_trace: {
          capability_name: result.server?.name || serverId,
          capability_id: result.server?.id || `mcp.${serverId}`,
          kind: "mcp",
          agent: "Lead",
        },
      },
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "保存 MCP 配置失败",
      content: error.message,
    });
  }
  render();
}

async function loadSkillDetail(skillId) {
  try {
    state.skillDetail = await fetchJson(`/api/capabilities/skills/${encodeURIComponent(skillId)}`);
  } catch (error) {
    state.skillDetail = null;
    addTimelineEvent({ type: "error", title: "获取 Skill 详情失败", content: error.message });
  }
  state.skillEditing = false;
  state.rightTab = "capabilities";
  render();
}

async function saveSkillContent() {
  const skillId = state.skillDetail?.id;
  const content = document.querySelector("#skill-edit-textarea")?.value;
  if (!skillId || content == null) return;
  try {
    const result = await requestJson(`/api/capabilities/skills/${encodeURIComponent(skillId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    state.skillDetail = result;
    state.skillEditing = false;
    addTimelineEvent({ type: "capability_used", title: "Skill 已更新", content: `${result.name} 内容已保存。` });
    loadCapabilities();
    loadWorkspaceOverview();
  } catch (error) {
    addTimelineEvent({ type: "error", title: "保存 Skill 失败", content: error.message });
  }
  render();
}

async function deleteSkill(skillId) {
  if (!confirm("确认删除此 Skill？此操作不可撤销。")) return;
  try {
    await requestJson(`/api/capabilities/skills/${encodeURIComponent(skillId)}`, { method: "DELETE" });
    state.skillDetail = null;
    state.skillEditing = false;
    addTimelineEvent({ type: "capability_used", title: "Skill 已删除", content: `${skillId} 已从工作区移除。` });
    loadCapabilities();
    loadWorkspaceOverview();
  } catch (error) {
    addTimelineEvent({ type: "error", title: "删除 Skill 失败", content: error.message });
  }
  render();
}

function cancelSkillEdit() {
  state.skillEditing = false;
  render();
}

function scheduleCapabilityRecommendation(prompt) {
  if (state.capabilityRecommendationMuted) return;
  window.clearTimeout(recommendationTimer);
  recommendationTimer = window.setTimeout(() => {
    refreshCapabilityRecommendation(prompt);
  }, 350);
}

async function refreshCapabilityRecommendation(prompt) {
  const text = String(prompt || "").trim();
  if (!text) return;
  if (text !== String(state.prompt || "").trim()) return;
  try {
    const result = await requestJson("/api/capabilities/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    if (text !== String(state.prompt || "").trim()) return;
    state.capabilityRecommendation = normalizeCapabilityRecommendation(result);
  } catch {
    if (text !== String(state.prompt || "").trim()) return;
    state.capabilityRecommendation = inferLocalCapabilityRecommendation(text);
  }
  if (document.activeElement?.id === "prompt-input") {
    recommendationRenderDeferred = true;
    return;
  }
  render();
}



function inferLocalCapabilityRecommendation(prompt) {
  return inferLocalCapabilityRecommendationValue(prompt, {
    capabilityHub: state.capabilityHub,
    getCapabilityOptions,
    capabilityDisplayName,
  });
}





async function ensureConversation(prompt = state.prompt) {
  if (state.currentConversationId) return state.currentConversationId;
  const result = await requestJson("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, workspace_dir: state.workspaceDir || undefined }),
  });
  applyConversation(result.conversation || {}, { reset: false });
  return state.currentConversationId;
}

async function refreshConversationTeam(prompt = state.prompt, { renderAfter = true } = {}) {
  const text = String(prompt || "").trim();
  if (!text || !state.currentConversationId) return;
  const result = await requestJson(`/api/conversations/${encodeURIComponent(state.currentConversationId)}/team/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: text, workspace_dir: state.workspaceDir || undefined }),
  });
  const team = result.team || {};
  const recommendation = result.recommendation || team.recommendation || {};
  if (Array.isArray(team.members)) {
    state.team = mapBackendTeam(team.members);
  }
  state.capabilityRecommendation = normalizeCapabilityRecommendation({
    agents: state.team.map((member) => member.name),
    capabilities: recommendation.capabilities || [],
    reasons: recommendation.reasons || [],
    summary: recommendation.summary,
  });
  upsertRun({
    id: state.currentConversationId,
    kind: "conversation",
    conversationId: state.currentConversationId,
    title: runTitle(text, "新会话"),
    status: "idle",
    time: nowTime(),
    prompt: text,
    agentCount: state.team.length,
  });
  if (renderAfter) render();
}

async function runBenchmark(benchmarkId) {
  const benchmark = state.benchmarks.find((item) => item.id === benchmarkId);
  if (!benchmark) return;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  clearReplayState();

  state.status = "running";
  state.currentThreadId = "pending";
  state.activeTab = "artifacts";
  resetRunView(`运行基准任务：${benchmark.title}`);
  addMessage({
    role: "assistant",
    author: "Lead Agent",
    content: "我会按固定验收标准执行 Benchmark，并归档评分、测试、Diff 和交付物。",
  });
  render();

  try {
    const run = await requestJson("/api/benchmarks/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ benchmark_id: benchmarkId, workspace_dir: state.workspaceDir || undefined }),
    });
    state.currentThreadId = run.thread_id;
    state.runs.unshift({
      id: run.thread_id,
      title: `Benchmark: ${run.title}`,
      status: "running",
      time: nowTime(),
    });
    addTimelineEvent({
      type: "run_started",
      title: "Benchmark 已启动",
      content: `${run.title} · ${run.thread_id}`,
    });
    await refreshWorkspaceData({ allowEmpty: false });
    connectEvents(run.thread_id);
  } catch (error) {
    state.status = "failed";
    addTimelineEvent({
      type: "error",
      title: "Benchmark 启动失败",
      content: error.message,
    });
  }
}

async function loadMemoryProfile() {
  try {
    const profile = await fetchJson("/api/preferences/profile");
    state.memoryProfile = profile;
    render();
  } catch {
    render();
  }
}

async function loadRecoveryCenter() {
  try {
    state.recoveryCenter = await fetchJson("/api/recovery");
    render();
  } catch {
    render();
  }
}

async function createPreference() {
  const preferenceType = document.querySelector("#preference-type")?.value;
  const content = document.querySelector("#preference-content")?.value.trim();
  if (!preferenceType || !content) {
    addTimelineEvent({
      type: "error",
      title: "偏好内容为空",
      content: "请先填写一条希望 nanoCursor 记住的偏好。",
    });
    return;
  }

  try {
    const result = await requestJson("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preference_type: preferenceType,
        content,
        importance: 8,
      }),
    });
    state.memoryProfile = result.profile;
    state.rightTab = "preferences";
    addTimelineEvent({
      type: "memory_updated",
      title: "偏好已保存",
      content,
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "保存偏好失败",
      content: error.message,
    });
  }
}



function mapBackendTasks(tasks) {
  return mapBackendTasksValue(tasks, { inferTaskCapabilities });
}

function tasksFromExecutionPlan(executionPlan) {
  return tasksFromExecutionPlanValue(executionPlan, { inferTaskCapabilities });
}

function syncTasksFromExecutionPlan(executionPlan) {
  tasksFromExecutionPlan(executionPlan).forEach((task) => upsertTask(task));
}



function taskForStageId(stageId) {
  if (!stageId) return null;
  return state.tasks.find((task) => task.id === stageId || String(task.id || "").endsWith(`-${stageId}`));
}

function mapBackendTeam(members) {
  return mapBackendTeamValue(members, { agentToneFromName });
}

function upsertTask(task) {
  if (!task?.id) return;
  const existing = state.tasks.find((item) => item.id === task.id);
  const normalized = normalizeTask(task);
  if (!normalized) return;

  if (existing) {
    if (["completed", "failed", "cancelled", "skipped"].includes(existing.status) && ["pending", "in_progress", "running"].includes(normalized.status)) {
      normalized.status = existing.status;
    }
    Object.assign(existing, normalized);
  } else {
    state.tasks.push(normalized);
  }
  state.metrics.tasks = state.tasks.length;
}

function patchTask(taskId, patch) {
  if (!taskId) return;
  const task = state.tasks.find((item) => item.id === taskId);
  if (task) {
    const normalized = normalizeTask({ ...task, ...patch, id: taskId });
    if (normalized) Object.assign(task, normalized);
    return;
  }
  const normalized = normalizeTask({ title: patch.title || taskId, ...patch, id: taskId });
  if (!normalized) return;
  state.tasks.push(normalized);
  state.metrics.tasks = state.tasks.length;
}

function settleTasksForRunStatus(status) {
  if (status !== "completed") return;
  state.tasks.forEach((task) => {
    if (["pending", "in_progress", "running"].includes(task.status)) {
      task.status = "completed";
    }
  });
}

function patchStageTask(stageUpdate = {}) {
  const stageId = stageUpdate.stage_id || stageUpdate.stageId || "";
  if (!stageId) return;
  const existing = taskForStageId(stageId);
  const taskId = existing?.id || `stage-${String(state.tasks.length + 1).padStart(2, "0")}-${stageId}`;
  patchTask(taskId, {
    title: stageUpdate.title || existing?.title || stageId,
    description: stageUpdate.description || existing?.description || "",
    status: stageUpdate.status || existing?.status || "pending",
    owner: stageUpdate.owner || existing?.owner || "Agent",
    failure: stageUpdate.failure || existing?.failure || "",
  });
}

function attachToolEvidenceToTask(stageId, evidence) {
  const task = taskForStageId(stageId);
  if (!task) return;
  const toolEvidence = Array.isArray(task.toolEvidence) ? task.toolEvidence : [];
  task.toolEvidence = [...toolEvidence, evidence].slice(-12);
}

function normalizeTask(task) {
  return normalizeTaskValue(task, { inferTaskCapabilities });
}

function upsertFile(file) {
  const path = typeof file === "string" ? file : file?.path;
  if (!path) return;
  state.files = state.files.map((item) => ({ ...item, active: false }));
  const existing = state.files.find((item) => item.path === path);
  if (existing) {
    existing.active = true;
    existing.type = fileType(path);
  } else {
    state.files.unshift({
      path,
      type: fileType(path),
      active: true,
    });
  }
  state.metrics.files = state.files.length;
}

async function refreshWorkspaceData({ allowEmpty = false, announce = false, includeRunState = true } = {}) {
  const hasFocusedRun = Boolean(state.currentThreadId && state.currentThreadId !== "pending");
  const shouldUpdateRunState = includeRunState && !hasFocusedRun;
  const results = await Promise.allSettled([
    fetchJson("/api/files"),
    shouldUpdateRunState ? fetchJson("/api/tasks") : Promise.resolve({ tasks: [] }),
    shouldUpdateRunState ? fetchJson("/api/team") : Promise.resolve({ members: [] }),
  ]);

  const [filesResult, tasksResult, teamResult] = results;

  if (filesResult.status === "fulfilled") {
    const files = filesResult.value.files || [];
    if (files.length || allowEmpty) {
      state.files = files.slice(0, 80).map((file, index) => ({
        path: file.path,
        type: fileType(file.path, file.is_dir),
        active: index === 0,
      }));
      state.metrics.files = files.filter((file) => !file.is_dir).length;
    }
  }

  if (shouldUpdateRunState && tasksResult.status === "fulfilled") {
    const tasks = tasksResult.value.tasks || [];
    if (tasks.length || allowEmpty) {
      state.tasks = mapBackendTasks(tasks);
      state.metrics.tasks = tasks.length;
    }
  }

  if (shouldUpdateRunState && teamResult.status === "fulfilled") {
    const members = teamResult.value.members || [];
    if (members.length || allowEmpty) {
      state.team = mapBackendTeam(members);
    }
  }

  if (announce) {
    addTimelineEvent({
      type: "metrics_updated",
      title: "工作区数据已同步",
      content: "文件、任务和团队状态已从后端刷新。",
    });
  } else {
    render();
  }
}

async function hydrateRunArtifacts(threadId, { refreshWorkspace = true } = {}) {
  const requests = [
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/diff`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/report`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/traceability`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/artifacts`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/recovery`),
    // R1-R6: delivery, changes, failures
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/delivery`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/changes`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/failures`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/agents?include_archived=${state.ephemeralAgents?.includeArchived ? "true" : "false"}`),
  ];
  if (refreshWorkspace) {
    requests.push(refreshWorkspaceData({ allowEmpty: true, includeRunState: false }));
  }

  const results = await Promise.allSettled(requests);

  const [
    diffResult, reportResult, traceabilityResult, artifactsResult, recoveryResult,
    deliveryResult, changesResult, failuresResult, agentsResult,
  ] = results;

  if (diffResult.status === "fulfilled") {
    const diffInfo = diffResult.value;
    const diffText =
      diffInfo.diff ||
      `No diff detected for ${threadId}.\n\nChanged files: ${(diffInfo.changed_files || [])
        .map((file) => file.path)
        .join(", ") || "none"}`;
    setDiffState(diffText, diffInfo.changed_files || []);
    if (Array.isArray(diffInfo.changed_files) && diffInfo.changed_files.length) {
      state.report.changedFiles = diffInfo.changed_files.map((file) => file.path);
      state.metrics.files = diffInfo.changed_files.length;
    }
  }

  if (reportResult.status === "fulfilled") {
    const report = reportResult.value;
    state.report.summary = report.summary || state.report.summary;
    state.report.markdown = report.markdown || "";
    state.report.changedFiles = (report.changed_files || state.report.changedFiles).map((item) =>
      typeof item === "string" ? item : item.path,
    );
    state.report.risks = report.risks?.length ? report.risks : state.report.risks;
  }

  if (traceabilityResult.status === "fulfilled") {
    setTraceability(traceabilityResult.value);
  }

  if (artifactsResult.status === "fulfilled") {
    state.artifactCenter = artifactsResult.value;
  }

  if (recoveryResult.status === "fulfilled") {
    state.recoveryCenter = recoveryResult.value;
  }

  // R1: delivery contract
  if (deliveryResult.status === "fulfilled") {
    const dc = deliveryResult.value;
    if (dc) {
      state.report.delivery = dc;
      state.report.summary = dc.summary || state.report.summary;
      state.report.changedFiles = (dc.changed_files || []).map((f) => f.path || f);
      state.report.risks = dc.risks || state.report.risks;
      state.currentRunStatus = dc.status;
    }
  }

  // R2: change set
  if (changesResult.status === "fulfilled") {
    const cs = changesResult.value;
    if (cs && cs.files) {
      state.diffFiles = cs.files.map((f) => ({
        path: f.path,
        changeType: f.change_type,
        risk: f.risk,
        additions: f.additions,
        deletions: f.deletions,
      }));
      state.metrics.files = cs.files.length;
    }
  }

  // R4: failure records
  if (failuresResult.status === "fulfilled") {
    const failures = failuresResult.value;
    if (failures && failures.failures) {
      state.recoveryCenter = state.recoveryCenter || {};
      state.recoveryCenter.failures = failures.failures;
    }
  }

  if (agentsResult.status === "fulfilled") {
    state.ephemeralAgents = normalizeEphemeralAgentsResult(
      {
        ...agentsResult.value,
        includeArchived: Boolean(state.ephemeralAgents?.includeArchived),
        suggestions: state.ephemeralAgents?.suggestions || [],
      },
      state.ephemeralAgents || blankEphemeralAgents(),
    );
  }

  render();
}

async function runPrompt(prompt, options = {}) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  clearReplayState();

  state.status = "running";
  state.prompt = prompt;
  state.currentThreadId = "pending";
  resetRunView(prompt);
  addMessage({
    role: "assistant",
    author: "Lead Agent",
    content: "我正在连接后端 Agent Runtime，并准备接收实时事件。",
  });
  render();

  try {
    if (!options.demo) {
      await ensureConversation(prompt);
      await saveConversationTeam(teamToBackendMembers());
    }
    state.runs = state.runs.filter((runItem) => !runItem.localOnly);
    const endpoint = options.demo
      ? "/api/runs/demo"
      : `/api/conversations/${encodeURIComponent(state.currentConversationId)}/runs`;
    const result = await requestJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, workspace_dir: state.workspaceDir || undefined }),
    });
    const run = result.run || result;
    if (result.conversation) {
      applyConversation(result.conversation, { reset: false });
    }
    state.currentThreadId = run.thread_id;
    upsertRun({
      id: run.thread_id,
      kind: "run",
      title: runTitle(prompt, "新任务"),
      status: "running",
      time: nowTime(),
      prompt,
    });
    if (state.currentConversationId) {
      upsertRun({
        id: state.currentConversationId,
        kind: "conversation",
        conversationId: state.currentConversationId,
        threadId: run.thread_id,
        title: runTitle(prompt, "新会话"),
        status: "running",
        time: nowTime(),
        prompt,
        agentCount: state.team.length,
      });
    }
    addTimelineEvent({
      type: "run_started",
      title: "后端运行已启动",
      content: `Thread: ${run.thread_id}`,
    });
    showToast("success", "运行已启动", shortId(run.thread_id, ""));
    await refreshWorkspaceData({ allowEmpty: false });
    connectEvents(run.thread_id);
  } catch (error) {
    state.status = "failed";
    addMessage({
      role: "assistant",
      author: "Lead Agent",
      content: `后端暂时不可用：${error.message}。当前页面仍可使用 Demo 数据展示工作台形态。`,
    });
    addTimelineEvent({
      type: "error",
      title: "连接失败",
      content: `${API_CANDIDATES.join(" 或 ")} 未返回可用响应。`,
    });
    showToast("error", "运行启动失败", error.message);
  }
}

function connectEvents(threadId) {
  eventSource = new EventSource(apiClient.eventSourceUrl(`/api/runs/${encodeURIComponent(threadId)}/events`));

  eventSource.onmessage = (event) => {
    if (event.data?.trim()) {
      handleAgentEvent(JSON.parse(event.data));
    }
  };

  [
    "run_started",
    "assistant_message",
    "plan_created",
    "approval_requested",
    "approval_resolved",
    "tool_approval_required",
    "run_waiting_approval",
    "stage_updated",
    "task_created",
    "task_updated",
    "team_updated",
    "tool_call_finished",
    "file_changed",
    "diff_updated",
    "test_finished",
    "preview_started",
    "report_ready",
    "traceability_ready",
    "benchmark_finished",
    "metrics_updated",
    "ephemeral_agent_spawned",
    "ephemeral_agent_completed",
    "ephemeral_agent_archived",
    "ephemeral_agent_expired",
    "done",
    "error",
  ].forEach((type) => {
    eventSource.addEventListener(type, (event) => {
      handleAgentEvent(JSON.parse(event.data));
    });
  });

  eventSource.onerror = () => {
    eventSource?.close();
    if (state.status !== "running") return;

    fetchJson(`/api/runs/${encodeURIComponent(threadId)}`)
      .then(async (session) => {
        const status = session.status || "running";
        if (["completed", "failed", "cancelled"].includes(status)) {
          state.status = status;
          settleTasksForRunStatus(status);
          updateCurrentRunStatus(status);
          await hydrateRunArtifacts(threadId);
          return;
        }
        addTimelineEvent({
          type: "metrics_updated",
          title: "事件流已断开",
          content: "后端运行记录仍存在，可通过同步或历史运行恢复状态。",
        });
      })
      .catch((error) => {
        addTimelineEvent({
          type: "error",
          title: "事件流连接失败",
          content: `无法确认 run 状态：${error.message}`,
        });
        state.status = "failed";
        updateCurrentRunStatus("failed");
        render();
      });
  };
}

function handleAgentEvent(event, options = {}) {
  handleAgentEventAction(event, options, {
    state,
    seenEventIds,
    nowTime,
    addMessage,
    upsertTask,
    patchStageTask,
    blankApproval,
    normalizeApprovalTasks,
    attachToolEvidenceToTask,
    patchTask,
    mapBackendTeam,
    upsertEphemeralAgent,
    upsertFile,
    setDiffState,
    setTraceability,
    settleTasksForRunStatus,
    updateCurrentRunStatus,
    hydrateRunArtifacts,
    refreshReplayEvents,
    closeEventSource,
    render,
  });
}

function closeEventSource() {
  eventSource?.close();
}

function updateCurrentRunStatus(status) {
  updateCurrentRunStatusAction(state, status, nowTime);
}
function buildReportText() {
  return buildReportTextValue(state.report);
}


render();
loadWorkspaceState().finally(() => {
  loadWorkspaceOverview();
  loadRunHistory();
  loadCapabilities();
  loadMcpConfig();
  loadBenchmarks();
  loadMemoryProfile();
  loadRecoveryCenter();
  loadWorkspaceSettings();
  loadRecentProjects();
  refreshWorkspaceData({ allowEmpty: false }).catch(() => {
    render();
  });
});
