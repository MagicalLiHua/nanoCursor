import {
  STORAGE_KEYS,
  getStorageValue,
  saveLayoutMode,
  saveLayoutPreference as persistLayoutPreferenceValue,
} from "./core/storage.js";
import { createApiClient } from "./core/apiClient.js";
import { bootstrapApp } from "./app/bootstrap.js";
import { createBusyController } from "./controllers/busyController.js";
import { createCapabilityController } from "./controllers/capabilityController.js";
import { createCommandController } from "./controllers/commandController.js";
import { createEphemeralAgentController } from "./controllers/ephemeralAgentController.js";
import { createLayoutController } from "./controllers/layoutController.js";
import { createRecommendationController } from "./controllers/recommendationController.js";
import { createReplayController } from "./controllers/replayController.js";
import { createRunLifecycleController } from "./controllers/runLifecycleController.js";
import { createRunStateController } from "./controllers/runStateController.js";
import { createToastController } from "./controllers/toastController.js";
import { createWorkspaceSessionController } from "./controllers/workspaceSessionController.js";
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
import {
  hydrateRunArtifacts as hydrateRunArtifactsAction,
  mapTraceability,
} from "./hydrators/runHydrator.js";
import { demoState } from "./state/demoState.js";
import {
  fileType,
  mapBackendTasks as mapBackendTasksValue,
  mapBackendTeam as mapBackendTeamValue,
  normalizeTask as normalizeTaskValue,
  tasksFromExecutionPlan as tasksFromExecutionPlanValue,
} from "./state/mappers.js";
import {
  blankApproval as blankApprovalValue,
  blankArtifactCenter,
  blankEphemeralAgents,
  blankRecoveryCenter,
  blankReport,
} from "./state/runDefaults.js";
import {
  capabilityDisplayName as capabilityDisplayNameValue,
  eventKind as eventKindValue,
  getCapabilityOptions as getCapabilityOptionsValue,
  inferTaskCapabilities as inferTaskCapabilitiesValue,
  renderEventCapabilityTrace as renderEventCapabilityTraceValue,
  visibleRecentProjects as visibleRecentProjectsValue,
} from "./state/selectors.js";
import {
  loadRecentProjects as loadRecentProjectsAction,
} from "./actions/workspaceActions.js";
import { recommendCapabilities } from "./actions/capabilityActions.js";
import {
  createPreference as createPreferenceAction,
  loadBenchmarks as loadBenchmarksAction,
  loadMemoryProfile as loadMemoryProfileAction,
  loadRecoveryCenter as loadRecoveryCenterAction,
  loadReplayEvents,
  loadRunArtifactsBundle,
  loadRunSession,
  loadWorkspaceDataSnapshot,
} from "./actions/runActions.js";

const configuredApiBase = getStorageValue("apiBase");
const RECOMMENDATION_MUTED_KEY = STORAGE_KEYS.recommendationMuted;
const API_CANDIDATES = configuredApiBase
  ? [configuredApiBase]
  : ["http://127.0.0.1:8100", "http://127.0.0.1:8101", "http://127.0.0.1:8102"];
const apiClient = createApiClient(API_CANDIDATES);

const state = structuredClone(demoState);
let eventSource = null;
let seenEventIds = new Set();

const layoutController = createLayoutController({
  state,
  render,
  persistLayoutPreference: persistLayoutPreferenceValue,
  saveLayoutMode,
});
const {
  captureFocusedField,
  ensureUiState,
  layoutClass,
  restoreFocusedField,
  saveLayoutPreference,
  setLayoutMode,
} = layoutController;
const replayController = createReplayController({
  state,
  createBlankReplay: () => structuredClone(demoState.replay),
  render,
  resetRunView,
  hydrateRunArtifacts,
  handleAgentEvent,
});
const {
  applyRunStateSnapshot,
  clearReplayState,
  pauseReplay,
  resetReplayToStart,
  setReplayEvents,
  setReplaySpeed,
  startReplay,
  stopReplayTimer,
} = replayController;
const toastController = createToastController({
  state,
  ensureUiState,
  render,
  renderToastView,
});
const {
  renderToast,
  showToast,
} = toastController;
const busyController = createBusyController({
  state,
  ensureUiState,
  render,
  showToast,
});
const {
  isActionBusy,
  withBusyAction,
} = busyController;
const runStateController = createRunStateController({
  state,
  render,
  addTimelineEvent,
  fetchJson,
  loadWorkspaceDataSnapshot,
  fileType,
  mapBackendTasks,
  mapBackendTeam,
  normalizeTask,
  tasksFromExecutionPlan,
});
const {
  attachToolEvidenceToTask,
  patchStageTask,
  patchTask,
  refreshWorkspaceData,
  settleTasksForRunStatus,
  syncTasksFromExecutionPlan,
  upsertFile,
  upsertTask,
} = runStateController;
const workspaceSessionController = createWorkspaceSessionController({
  state,
  storageKeys: STORAGE_KEYS,
  requestJson,
  fetchJson,
  render,
  addTimelineEvent,
  showToast,
  closeEventSource,
  stopReplayTimer,
  clearReplayState,
  resetRunView,
  refreshWorkspaceData,
  mapBackendTeam,
  normalizeCapabilityRecommendation,
  loadCapabilities: () => loadCapabilities(),
  loadBenchmarks,
  loadMemoryProfile,
  loadRecoveryCenter,
  loadWorkspaceSettings,
  loadRecentProjects,
  nowTime,
  formatTime,
  runTitle,
  shortPath,
});
const {
  applyConversation,
  conversationQuery,
  createCustomAgent,
  ensureConversation,
  loadRunHistory,
  loadWorkspaceOverview,
  loadWorkspaceState,
  openWorkspace,
  refreshConversationTeam,
  removeTeamMember,
  saveConversationTeam,
  startNewSession,
  teamToBackendMembers,
  upsertRun,
} = workspaceSessionController;
const commandController = createCommandController({
  state,
  ensureUiState,
  render,
  getExecutionContext: () => ({
    withBusyAction,
    startNewSession,
    shortPath,
    showToast,
    render,
    refreshWorkspaceData,
    loadWorkspaceOverview,
    saveLayoutPreference,
    setLayoutMode,
  }),
});
const {
  closeCommandPalette,
  executeCommand,
  filteredCommandItems,
  openCommandPalette,
  renderCommandPalette,
  setCommandQuery,
} = commandController;
const recommendationController = createRecommendationController({
  state,
  render,
  requestJson,
  mutedStorageKey: RECOMMENDATION_MUTED_KEY,
  recommendCapabilities,
  normalizeCapabilityRecommendation,
  inferLocalCapabilityRecommendation: (prompt) =>
    inferLocalCapabilityRecommendationValue(prompt, {
      capabilityHub: state.capabilityHub,
      getCapabilityOptions,
      capabilityDisplayName,
    }),
});
const {
  dismissRecommendation,
  getRecommendationRenderDeferred,
  inferLocalCapabilityRecommendation,
  markRecommendationTyping,
  scheduleCapabilityRecommendation,
  setRecommendationRenderDeferred,
  toggleRecommendationDetail,
} = recommendationController;
const capabilityController = createCapabilityController({
  state,
  render,
  requestJson,
  fetchJson,
  withBusyAction,
  showToast,
  addTimelineEvent,
  loadWorkspaceOverview,
  inferLocalCapabilityRecommendation,
});
const {
  cancelSkillEdit,
  deleteSkill,
  importCustomSkill,
  installMcpPreset,
  loadCapabilities,
  loadMcpConfig,
  loadMcpTools,
  loadSkillDetail,
  saveMcpServerConfig,
  saveSkillContent,
  validateMcpConfig,
} = capabilityController;
const ephemeralAgentController = createEphemeralAgentController({
  state,
  render,
  fetchJson,
  requestJson,
  addTimelineEvent,
  blankEphemeralAgents,
});
const {
  archiveEphemeralAgent,
  completeEphemeralAgent,
  isEphemeralThreadReady,
  refreshEphemeralAgents,
  spawnEphemeralAgent,
  suggestEphemeralAgents,
  upsertEphemeralAgent,
} = ephemeralAgentController;
const runLifecycleController = createRunLifecycleController({
  state,
  fetchJson,
  requestJson,
  apiCandidates: API_CANDIDATES,
  render,
  addTimelineEvent,
  addMessage,
  closeEventSource,
  stopReplayTimer,
  clearReplayState,
  resetRunView,
  setReplayEvents,
  handleAgentEvent,
  hydrateRunArtifacts,
  updateCurrentRunStatus,
  syncTasksFromExecutionPlan,
  refreshWorkspaceData,
  connectEvents,
  showToast,
  ensureConversation,
  saveConversationTeam,
  teamToBackendMembers,
  applyConversation,
  conversationQuery,
  startNewSession,
  upsertRun,
  mapBackendTeam,
  nowTime,
  formatTime,
  runTitle,
  shortId,
});
const {
  restoreRun,
  runBenchmark,
  runPrompt,
} = runLifecycleController;

function visibleRecentProjects(limit = 6) {
  return visibleRecentProjectsValue(state, limit);
}

function getCapabilityOptions() {
  return getCapabilityOptionsValue(state);
}

function capabilityDisplayName(capabilityId) {
  return capabilityDisplayNameValue(state, capabilityId);
}

function renderEventCapabilityTrace(event) {
  return renderEventCapabilityTraceValue(state, event);
}

function inferTaskCapabilities(task) {
  return inferTaskCapabilitiesValue(task);
}

function eventKind(type) {
  return eventKindValue(type);
}

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
  layoutController.bindGlobalShortcutsOnce({ openCommandPalette, closeCommandPalette });
  bindEvents();
  restoreFocusedField(focusedField);
  resizePromptInput();
  scrollToLatestMessage();
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
  state.recentProjects = await loadRecentProjectsAction({ fetchJson });
  renderRecentProjectsList();
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
  return renderPreviewContent(state.previewUrl || "");
}

function renderReport() {
  return renderReportView(state);
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
    getRecommendationRenderDeferred,
    setRecommendationRenderDeferred,
    dismissRecommendation,
    toggleRecommendationDetail,
    markRecommendationTyping,
    openCommandPalette,
    closeCommandPalette,
    ensureUiState,
    setCommandQuery,
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

async function refreshReplayEvents(threadId, { prompt = state.prompt, startedAt = "" } = {}) {
  if (!threadId || threadId === "pending") return;
  try {
    const events = await loadReplayEvents({ fetchJson, threadId });
    setReplayEvents(events, { prompt, startedAt });
    applyRunStateSnapshot(events);
    render();
  } catch {
    // Replay is an enhancement; the live run view remains usable without it.
  }
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

async function loadBenchmarks() {
  const benchmarks = await loadBenchmarksAction({ fetchJson });
  if (benchmarks.length) {
    state.benchmarks = benchmarks;
  }
  render();
}

async function loadMemoryProfile() {
  try {
    state.memoryProfile = await loadMemoryProfileAction({ fetchJson });
    render();
  } catch {
    render();
  }
}

async function loadRecoveryCenter() {
  try {
    state.recoveryCenter = await loadRecoveryCenterAction({ fetchJson });
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
    const result = await createPreferenceAction({ requestJson, preferenceType, content });
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

function mapBackendTeam(members) {
  return mapBackendTeamValue(members, { agentToneFromName });
}

function normalizeTask(task) {
  return normalizeTaskValue(task, { inferTaskCapabilities });
}

async function hydrateRunArtifacts(threadId, { refreshWorkspace = true } = {}) {
  await hydrateRunArtifactsAction({
    state,
    fetchJson,
    threadId,
    loadRunArtifactsBundle,
    refreshWorkspaceData,
    setDiffState,
    setTraceability,
    render,
    refreshWorkspace,
  });
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

    loadRunSession({ fetchJson, threadId })
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
  eventSource = null;
}

function updateCurrentRunStatus(status) {
  updateCurrentRunStatusAction(state, status, nowTime);
}
function buildReportText() {
  return buildReportTextValue(state.report);
}

bootstrapApp({
  render,
  loadWorkspaceState,
  loadWorkspaceOverview,
  loadRunHistory,
  loadCapabilities,
  loadMcpConfig,
  loadBenchmarks,
  loadMemoryProfile,
  loadRecoveryCenter,
  loadWorkspaceSettings,
  loadRecentProjects,
  refreshWorkspaceData,
});
