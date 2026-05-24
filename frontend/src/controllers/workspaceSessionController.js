import {
  createConversationDraft,
  loadRunHistorySnapshot,
  loadWorkspaceOverview as loadWorkspaceOverviewAction,
  loadWorkspaceState as loadWorkspaceStateAction,
  openWorkspacePath,
} from "../actions/workspaceActions.js";
import {
  createTeamAgent,
  recommendConversationTeam,
  saveConversationTeam as saveConversationTeamAction,
} from "../actions/teamActions.js";
import {
  mapConversationItem as mapConversationItemValue,
  mapRunHistoryItem as mapRunHistoryItemValue,
} from "../state/mappers.js";

export function createWorkspaceSessionController({
  state,
  storageKeys,
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
  loadCapabilities,
  loadBenchmarks,
  loadMemoryProfile,
  loadRecoveryCenter,
  loadWorkspaceSettings,
  loadRecentProjects,
  nowTime,
  formatTime,
  runTitle,
  shortPath,
}) {
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

  function upsertRun(run) {
    if (!run?.id) return;
    const index = state.runs.findIndex((item) => item.id === run.id);
    if (index >= 0) {
      state.runs[index] = { ...state.runs[index], ...run };
    } else {
      state.runs.unshift(run);
    }
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

  async function loadRunHistory() {
    const snapshot = await loadRunHistorySnapshot({
      fetchJson,
      workspaceDir: state.workspaceDir,
      existingRuns: state.runs,
      mapRunHistoryItem,
      mapConversationItem,
    });
    if (snapshot) {
      state.conversations = snapshot.conversations;
      state.runs = snapshot.runs;
    }
    render();
  }

  async function loadWorkspaceState() {
    const snapshot = await loadWorkspaceStateAction({ fetchJson });
    if (!snapshot) return;
    state.workspaceMeta = snapshot.meta;
    if (snapshot.current) {
      state.workspaceDir = snapshot.current;
      state.workspaceInput = snapshot.current;
      localStorage.setItem(storageKeys.workspaceDir, snapshot.current);
    }
  }

  async function loadWorkspaceOverview() {
    state.projectOverview = await loadWorkspaceOverviewAction({
      fetchJson,
      workspaceDir: state.workspaceDir,
      previousOverview: state.projectOverview,
    });
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
      const result = await openWorkspacePath({ requestJson, path: dir });
      state.workspaceDir = result.path || dir;
      state.workspaceInput = state.workspaceDir;
      state.ui.workspacePickerOpen = false;
      localStorage.setItem(storageKeys.workspaceDir, state.workspaceDir);
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
    closeEventSource();
    stopReplayTimer();
    clearReplayState();

    let id = draftId || `draft-${Date.now().toString(36)}`;
    let conversation = null;
    if (!draftId) {
      try {
        conversation = await createConversationDraft({
          requestJson,
          workspaceDir: state.workspaceDir,
        });
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

  async function ensureConversation(prompt = state.prompt) {
    if (state.currentConversationId) return state.currentConversationId;
    const conversation = await createConversationDraft({
      requestJson,
      workspaceDir: state.workspaceDir,
      prompt,
    });
    applyConversation(conversation || {}, { reset: false });
    return state.currentConversationId;
  }

  async function saveConversationTeam(members) {
    await ensureConversation(state.prompt);
    const result = await saveConversationTeamAction({
      requestJson,
      conversationId: state.currentConversationId,
      members,
      workspaceDir: state.workspaceDir,
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

      const result = await createTeamAgent({ requestJson, name, role, goal, tools, capabilities });
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

  async function refreshConversationTeam(prompt = state.prompt, { renderAfter = true } = {}) {
    const text = String(prompt || "").trim();
    if (!text || !state.currentConversationId) return;
    const result = await recommendConversationTeam({
      requestJson,
      conversationId: state.currentConversationId,
      prompt: text,
      workspaceDir: state.workspaceDir,
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

  return {
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
  };
}
