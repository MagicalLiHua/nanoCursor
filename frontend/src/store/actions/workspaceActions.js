import { getApiClient } from "../../core/sharedApi.js";
import { nowTime, formatTime, runTitle, shortPath } from "../../core/format.js";
import { STORAGE_KEYS } from "../../core/storage.js";
import { mapBackendTeam as mapBackendTeamBase, mapConversationItem, mapRunHistoryItem } from "../../state/mappers.js";
import { agentToneFromName } from "../../core/format.js";
import {
  createConversationDraft,
  loadRunHistorySnapshot,
  loadWorkspaceOverview as loadWorkspaceOverviewApi,
  loadWorkspaceState as loadWorkspaceStateApi,
  openWorkspacePath,
} from "../../actions/workspaceActions.js";
import {
  loadConversation,
  saveConversationTeam as saveConversationTeamApi,
  recommendConversationTeam,
} from "../../actions/teamActions.js";
import { loadWorkspaceDataSnapshot } from "../../actions/runActions.js";
import { normalizeTask as normalizeTaskBase, tasksFromExecutionPlan, mapBackendTasks } from "../../state/mappers.js";
import { inferTaskCapabilities } from "../../state/selectors.js";

function mapBackendTeam(members) {
  return mapBackendTeamBase(members, { agentToneFromName });
}

function normalizeTask(task) {
  return normalizeTaskBase(task, { inferTaskCapabilities });
}

export function createWorkspaceActions(set, get) {
  function upsertRun(run) {
    if (!run?.id) return;
    set((state) => {
      const index = state.runs.findIndex((item) => item.id === run.id);
      if (index >= 0) {
        const next = [...state.runs];
        next[index] = { ...next[index], ...run };
        return { runs: next };
      }
      return { runs: [run, ...state.runs] };
    });
  }

  function applyConversation(conversation, { reset = false } = {}) {
    if (!conversation?.conversation_id) return;
    const state = get();
    const updates = {
      currentConversationId: conversation.conversation_id,
      currentThreadId: conversation.current_thread_id || conversation.conversation_id,
      status: conversation.status === "draft" ? "idle" : conversation.status || "idle",
      prompt: conversation.prompt || state.prompt || "",
    };
    if (conversation.team?.members?.length) {
      updates.team = mapBackendTeam(conversation.team.members);
    }
    set(updates);
    upsertRun(mapConversationItem(conversation, { runTitle, formatTime }));
    if (reset) {
      get().resetRunView("");
      set({
        messages: [{
          role: "assistant",
          author: "Lead Agent",
          time: nowTime(),
          content: state.workspaceDir
            ? "新会话已创建。输入需求后，我会直接在当前项目中启动一次可追踪运行。"
            : "新会话已创建。建议先打开一个项目目录，再开始交付任务。",
        }],
      });
    }
  }

  function teamToBackendMembers(team) {
    const members = team || get().team;
    return members.map((member) => ({
      name: member.name,
      role: member.role,
      goal: member.goal || "",
      tools: Array.isArray(member.tools) ? member.tools : [],
      capabilities: Array.isArray(member.capabilities) ? member.capabilities : [],
      artifacts: Array.isArray(member.artifacts) ? member.artifacts : [],
    }));
  }

  async function ensureConversation(prompt) {
    const state = get();
    if (state.currentConversationId) return state.currentConversationId;
    const api = getApiClient();
    const conversation = await createConversationDraft({
      requestJson: api.requestJson,
      workspaceDir: state.workspaceDir,
      prompt: prompt || state.prompt,
    });
    applyConversation(conversation || {}, { reset: false });
    return get().currentConversationId;
  }

  async function saveConversationTeam(members) {
    const state = get();
    await ensureConversation(state.prompt);
    const api = getApiClient();
    const result = await saveConversationTeamApi({
      requestJson: api.requestJson,
      conversationId: get().currentConversationId,
      members,
      workspaceDir: state.workspaceDir,
    });
    const team = mapBackendTeam(result.team?.members || members);
    set({ team });
    upsertRun({
      id: get().currentConversationId,
      kind: "conversation",
      conversationId: get().currentConversationId,
      title: runTitle(state.prompt, "新会话"),
      status: state.status === "running" ? "running" : "idle",
      time: nowTime(),
      prompt: state.prompt,
      agentCount: team.length,
    });
  }

  async function startNewSession({ draftId = "", keepRunItem = false, announce = true } = {}) {
    const state = get();
    // Close SSE and replay
    if (state._closeEventSource) state._closeEventSource();

    let id = draftId || `draft-${Date.now().toString(36)}`;
    let conversation = null;
    if (!draftId) {
      try {
        const api = getApiClient();
        conversation = await createConversationDraft({
          requestJson: api.requestJson,
          workspaceDir: state.workspaceDir,
        });
        id = conversation?.conversation_id || id;
      } catch (error) {
        get().addTimelineEvent({
          type: "error",
          title: "会话后端暂不可用",
          content: `已创建本地草稿：${error.message}`,
        });
      }
    }

    const conversationId = conversation?.conversation_id || (id.startsWith("conv-") ? id : "");
    set({
      currentConversationId: conversationId,
      currentThreadId: id,
      status: "idle",
      prompt: "",
      activeTab: "report",
    });
    get().resetRunView("");
    set({
      messages: [{
        role: "assistant",
        author: "Lead Agent",
        time: nowTime(),
        content: get().workspaceDir
          ? "新会话已创建。输入需求后，我会直接启动运行，并在右侧展示任务、团队和临时 Agent。"
          : "新会话已创建。建议先打开一个项目目录，再开始交付任务。",
      }],
    });

    if (conversation) {
      applyConversation(conversation, { reset: false });
    }

    if (!keepRunItem) {
      set((s) => ({
        runs: s.runs.filter((run) => !run.localOnly),
      }));
      upsertRun({
        id,
        kind: conversation ? "conversation" : "run",
        conversationId: conversation?.conversation_id || "",
        title: conversation?.title || "新会话",
        status: "idle",
        time: nowTime(),
        prompt: "",
        localOnly: !conversation,
        agentCount: conversation?.team?.members?.length || get().team.length,
      });
    }

    if (announce) {
      refreshWorkspaceData({ allowEmpty: true, includeRunState: false }).catch(() => {});
    }
  }

  async function loadRunHistory() {
    const state = get();
    const api = getApiClient();
    const snapshot = await loadRunHistorySnapshot({
      fetchJson: api.fetchJson,
      workspaceDir: state.workspaceDir,
      existingRuns: state.runs,
      mapRunHistoryItem: (run) => mapRunHistoryItem(run, { runTitle, formatTime }),
      mapConversationItem: (conv) => mapConversationItem(conv, { runTitle, formatTime }),
    });
    if (snapshot) {
      set({ conversations: snapshot.conversations, runs: snapshot.runs });
    }
  }

  async function loadWorkspaceState() {
    const api = getApiClient();
    const snapshot = await loadWorkspaceStateApi({ fetchJson: api.fetchJson });
    if (!snapshot) return;
    const updates = { workspaceMeta: snapshot.meta };
    if (snapshot.current) {
      updates.workspaceDir = snapshot.current;
      updates.workspaceInput = snapshot.current;
      try { localStorage.setItem(STORAGE_KEYS.workspaceDir, snapshot.current); } catch {}
    }
    set(updates);
  }

  async function loadWorkspaceOverview() {
    const state = get();
    const api = getApiClient();
    const overview = await loadWorkspaceOverviewApi({
      fetchJson: api.fetchJson,
      workspaceDir: state.workspaceDir,
      previousOverview: state.projectOverview,
    });
    set({ projectOverview: overview });
  }

  async function openWorkspace() {
    const state = get();
    const dir = (state.workspaceInput || "").trim();
    if (!dir) {
      get().addTimelineEvent({
        type: "error",
        title: "工作区路径为空",
        content: "请输入项目目录的绝对路径。",
      });
      return;
    }

    try {
      const api = getApiClient();
      const result = await openWorkspacePath({ requestJson: api.requestJson, path: dir });
      const workspaceDir = result.path || dir;
      set({
        workspaceDir,
        workspaceInput: workspaceDir,
        ui: { ...state.ui, workspacePickerOpen: false },
      });
      try { localStorage.setItem(STORAGE_KEYS.workspaceDir, workspaceDir); } catch {}
      await startNewSession({ announce: false });
      get().addTimelineEvent({
        type: "workspace_opened",
        title: "项目目录已打开",
        content: workspaceDir,
      });
      get().showToast({ kind: "success", title: "项目目录已打开", content: shortPath(workspaceDir) });
      await Promise.allSettled([
        loadWorkspaceOverview(),
        loadRunHistory(),
        get().loadCapabilities?.() || Promise.resolve(),
        get().loadBenchmarks?.() || Promise.resolve(),
        get().loadMemoryProfile?.() || Promise.resolve(),
        get().loadRecoveryCenter?.() || Promise.resolve(),
        refreshWorkspaceData({ allowEmpty: true }),
      ]);
    } catch (error) {
      get().addTimelineEvent({
        type: "error",
        title: "打开项目目录失败",
        content: error.message,
      });
      get().showToast({ kind: "error", title: "打开失败", content: error.message });
    }
  }

  async function refreshWorkspaceData({ allowEmpty = false, includeRunState = true } = {}) {
    const state = get();
    const api = getApiClient();
    const snapshot = await loadWorkspaceDataSnapshot({
      fetchJson: api.fetchJson,
      includeRunState,
    });

    const updates = {};
    const { filesResult, tasksResult, teamResult } = snapshot;

    if (filesResult?.status === "fulfilled") {
      const files = Array.isArray(filesResult.value)
        ? filesResult.value
        : filesResult.value?.files || [];
      if (Array.isArray(files)) {
        // Store full file objects with is_dir, path, size, mtime
        updates.workspaceFiles = files.map((f) => ({
          path: typeof f === "string" ? f : f?.path || "",
          is_dir: f?.is_dir || false,
          size: f?.size || 0,
          mtime: f?.mtime || null,
        })).filter((f) => f.path);
        // Keep backward compatible files array (paths only)
        updates.files = updates.workspaceFiles.map((f) => f.path);
      }
    }

    if (tasksResult?.status === "fulfilled") {
      const rawTasks = tasksResult.value?.tasks || [];
      const mapped = mapBackendTasks(rawTasks, { inferTaskCapabilities });
      if (mapped.length || allowEmpty) {
        updates.tasks = mapped;
      }
    }

    if (teamResult?.status === "fulfilled") {
      const members = teamResult.value?.members;
      if (Array.isArray(members) && members.length) {
        updates.team = mapBackendTeam(members);
      }
    }

    set(updates);
    return updates;
  }

  function syncTasksFromExecutionPlan(executionPlan) {
    const tasks = tasksFromExecutionPlan(executionPlan, { inferTaskCapabilities });
    set((state) => {
      const existing = new Map(state.tasks.map((t) => [t.id, t]));
      for (const task of tasks) {
        if (task) existing.set(task.id, { ...(existing.get(task.id) || {}), ...task });
      }
      return { tasks: Array.from(existing.values()) };
    });
  }

  function normalizeTask(task) {
    return normalizeTaskBase(task, { inferTaskCapabilities });
  }

  async function loadRecentProjects() {
    const api = getApiClient();
    try {
      const result = await api.fetchJson("/api/workspace/recent");
      set({ recentProjects: result.recent || [] });
    } catch {
      // ignore
    }
  }

  return {
    upsertRun,
    applyConversation,
    teamToBackendMembers,
    ensureConversation,
    saveConversationTeam,
    startNewSession,
    loadRunHistory,
    loadWorkspaceState,
    loadWorkspaceOverview,
    openWorkspace,
    refreshWorkspaceData,
    syncTasksFromExecutionPlan,
    normalizeTask,
    loadRecentProjects,
  };
}
