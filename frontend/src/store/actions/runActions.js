import { getApiClient } from "../../core/sharedApi.js";
import { nowTime, formatTime, runTitle, shortId } from "../../core/format.js";
import { parseUnifiedDiff } from "../../core/diff.js";
import { agentToneFromName } from "../../core/format.js";
import { mapBackendTeam as mapBackendTeamBase, mapRunHistoryItem as mapRunHistoryItemBase } from "../../state/mappers.js";
import {
  startRun as startRunApi,
  cancelRun as cancelRunApi,
  startBenchmark as startBenchmarkApi,
  loadRunSessionAndEvents,
  loadRunArtifactsBundle as loadRunArtifactsBundleApi,
  loadReplayEvents,
  loadRunState as loadRunStateApi,
  loadRunSnapshot as loadRunSnapshotApi,
} from "../../actions/runActions.js";
import { loadConversation } from "../../actions/teamActions.js";
import { applyRunArtifactsBundle, applyRunSnapshot } from "../../hydrators/runHydrator.js";

function mapBackendTeam(members) {
  return mapBackendTeamBase(members, { agentToneFromName });
}

function isPlaceholderConversationTitle(title = "") {
  return !String(title || "").trim() || String(title).trim() === "新会话";
}

export function mapConversationMessages(conversation = {}) {
  const explicitMessages = Array.isArray(conversation.messages) ? conversation.messages : [];
  if (explicitMessages.length) {
    return explicitMessages
      .filter((message) => String(message?.content || "").trim())
      .map((message) => ({
        role: message.role === "assistant" ? "assistant" : "user",
        author: message.role === "assistant" ? `${message.agent || message.author || "Lead"} Agent` : "用户",
        time: formatTime(message.timestamp || message.created_at || message.updated_at) || "",
        content: message.content,
      }));
  }

  const records = Array.isArray(conversation.run_records) ? conversation.run_records : [];
  const messages = [];
  for (const record of records) {
    const isCurrentRun = Boolean(
      conversation.current_thread_id &&
      record?.thread_id === conversation.current_thread_id,
    );
    const prompt = String(record?.prompt || "").trim();
    if (prompt) {
      messages.push({
        role: "user",
        author: "用户",
        time: formatTime(record.started_at || record.updated_at) || "",
        content: prompt,
      });
    }
    const summary = String(record?.summary || record?.error || "").trim();
    // The current run snapshot carries the authoritative, untruncated reply.
    if (summary && !isCurrentRun) {
      messages.push({
        role: "assistant",
        author: "Lead Agent",
        time: formatTime(record.completed_at || record.updated_at) || "",
        content: summary,
      });
    }
  }
  return messages;
}

export function canStartRun(status = "idle") {
  return !["running", "waiting_approval", "cancelling"].includes(status);
}

export function createRunActions(set, get) {
  function buildConversationHistory(nextPrompt) {
    const state = get();
    const history = (state.messages || [])
      .filter((m) => ["user", "assistant"].includes(m.role) && String(m.content || "").trim())
      .slice(-10)
      .map((m) => ({ role: m.role, author: m.author, time: m.time, content: m.content }));
    history.push({ role: "user", author: "用户", time: nowTime(), content: nextPrompt });
    return history;
  }

  async function runPrompt(prompt, options = {}) {
    const api = getApiClient();
    const state = get();
    if (!canStartRun(state.status)) return false;

    // Close existing connections
    closeEventSourceSSE();
    set({ replay: { events: [], index: 0, speed: 1, status: "idle", prompt: "", startedAt: "" } });

    const historyMessages = buildConversationHistory(prompt);
    set({
      status: "running",
      runStartedAt: Date.now(),
      prompt: "",
      currentThreadId: "pending",
    });
    get().resetRunView(prompt);
    set({ messages: historyMessages });

    try {
      if (!options.demo) {
        await get().ensureConversation(prompt);
        const members = get().teamToBackendMembers();
        await get().saveConversationTeam(members);
      }

      set((s) => ({ runs: s.runs.filter((r) => !r.localOnly) }));

      const result = await startRunApi({
        requestJson: api.requestJson,
        conversationId: get().currentConversationId,
        prompt,
        workspaceDir: get().workspaceDir,
        messages: historyMessages.slice(0, -1).map((m) => ({
          role: m.role === "assistant" ? "assistant" : "user",
          content: m.content,
        })),
        demo: Boolean(options.demo),
      });

      const run = result.run || result;
      if (result.conversation) {
        get().applyConversation(result.conversation, { reset: false });
      }

      set({ prompt: "" });
      if (Array.isArray(result.runtime_team?.members) && result.runtime_team.members.length) {
        set({ team: mapBackendTeam(result.runtime_team.members) });
      }

      set({ currentThreadId: run.thread_id });
      get().persistActiveSession();

      const conversationId = get().currentConversationId;
      if (conversationId) {
        const existingConversation = get().runs.find((item) => item.id === conversationId);
        const title = isPlaceholderConversationTitle(existingConversation?.title)
          ? runTitle(prompt, "新会话")
          : existingConversation.title;
        const conversationPrompt = String(existingConversation?.prompt || "").trim()
          ? existingConversation.prompt
          : prompt;
        get().upsertRun({
          id: conversationId,
          kind: "conversation",
          conversationId,
          threadId: run.thread_id,
          title,
          status: "running",
          time: nowTime(),
          prompt: conversationPrompt,
          agentCount: get().team.length,
        });
      } else {
        get().upsertRun({
          id: run.thread_id,
          kind: "run",
          title: runTitle(prompt, "新任务"),
          status: "running",
          time: nowTime(),
          prompt,
        });
      }

      get().addTimelineEvent({
        type: "run_started",
        title: "后端运行已启动",
        content: `Thread: ${run.thread_id}`,
      });
      get().showToast({ kind: "success", title: "运行已启动", content: shortId(run.thread_id, "") });
      await get().refreshWorkspaceData({ allowEmpty: false });
      await refreshRunSnapshot(run.thread_id, { focusPanel: false, replaceMessages: false });
      connectEventsSSE(run.thread_id);
    } catch (error) {
      set({ status: "failed" });
      get().addMessage({
        role: "assistant",
        author: "Lead Agent",
        content: `后端暂时不可用：${error.message}。当前页面仍可使用 Demo 数据展示工作台形态。`,
      });
      get().addTimelineEvent({
        type: "error",
        title: "连接失败",
        content: "127.0.0.1:8100 未返回可用响应。",
      });
      get().showToast({ kind: "error", title: "运行启动失败", content: error.message });
    }
    return true;
  }

  async function restoreRun(threadId, options = {}) {
    if (!threadId) return false;
    const force = Boolean(options.force);
    const quiet = Boolean(options.quiet);
    const replaceMessages = options.replaceMessages !== false;
    const state = get();
    closeEventSourceSSE();

    const selectedRun = state.runs.find((r) => r.id === threadId);
    if (selectedRun?.kind === "conversation") {
      await restoreConversation(selectedRun.conversationId || selectedRun.id, { force });
      return true;
    }
    if (threadId === state.currentThreadId && !force) return true;

    if (selectedRun?.localOnly) {
      await get().startNewSession({ draftId: selectedRun.id, keepRunItem: true });
      return true;
    }

    set({
      currentThreadId: threadId,
      status: selectedRun?.status || "running",
      prompt: "",
      activeTab: "report",
    });
    get().persistActiveSession({ threadId });
    const preservedMessages = replaceMessages ? null : [...(get().messages || [])];
    get().resetRunView(selectedRun?.prompt || "");
    if (preservedMessages) {
      set({ messages: preservedMessages });
    }
    if (!quiet) {
      get().addTimelineEvent({
        type: "run_restoring",
        title: "正在恢复历史运行",
        content: selectedRun?.title || threadId,
      });
    }

    try {
      const api = getApiClient();
      let snapshot = null;
      try {
        snapshot = await loadRunSnapshotApi({ fetchJson: api.fetchJson, threadId });
      } catch {
        // Older backend fallback below.
      }

      if (snapshot) {
        applySnapshotToStore(snapshot, { replaceMessages });
        get().upsertRun({
          id: threadId,
          title: runTitle(snapshot.run?.prompt || selectedRun?.prompt, threadId),
          status: snapshot.run?.status || selectedRun?.status || get().status,
          time: formatTime(snapshot.run?.updated_at || snapshot.run?.created_at) || selectedRun?.time || "",
          mode: snapshot.run?.mode || selectedRun?.mode || "agenthub_delivery",
          prompt: snapshot.run?.prompt || selectedRun?.prompt || "",
          eventCount: Array.isArray(snapshot.timeline) ? snapshot.timeline.length : selectedRun?.eventCount || 0,
          changedFilesCount: snapshot.changes?.files_changed || selectedRun?.changedFilesCount || 0,
          hasDiff: Boolean(snapshot.changes?.files_changed || snapshot.outcome?.changes?.diff),
          hasReport: Boolean(snapshot.outcome?.report?.markdown || snapshot.outcome?.report?.summary),
          lastEventType: snapshot.timeline?.at?.(-1)?.type || selectedRun?.lastEventType || "",
        });
        if (["running", "waiting_approval", "cancelling"].includes(get().status)) {
          connectEventsSSE(threadId);
        }
        get().persistActiveSession();
        return true;
      }

      const { sessionResult, eventsResult } = await loadRunSessionAndEvents({ fetchJson: api.fetchJson, threadId });

      const session = sessionResult.status === "fulfilled" ? sessionResult.value : null;
      const prompt = session?.prompt || selectedRun?.prompt || "";
      if (session?.conversation_id) {
        set({ currentConversationId: session.conversation_id });
      }
      if (Array.isArray(session?.team) && session.team.length) {
        set({ team: mapBackendTeam(session.team) });
      }
      set({
        prompt: "",
        status: session?.status || selectedRun?.status || get().status,
      });

      get().resetRunView(prompt);
      if (session?.execution_plan) {
        get().syncTasksFromExecutionPlan(session.execution_plan);
        if (get().status === "running" && get().tasks.length) {
          set({ rightTab: "progress" });
        }
      }
      await refreshRunState(threadId, { focusPanel: false });
      if (prompt) {
        set((s) => ({
          messages: s.messages.map((m, i) =>
            i === 0 ? { ...m, time: formatTime(session?.created_at) || selectedRun?.time || "" } : m
          ),
        }));
      }

      get().upsertRun({
        id: threadId,
        title: runTitle(prompt, threadId),
        status: get().status,
        time: formatTime(session?.updated_at || session?.created_at) || selectedRun?.time || "",
        mode: session?.mode || selectedRun?.mode || "agenthub_delivery",
        prompt,
        eventCount: selectedRun?.eventCount || 0,
        changedFilesCount: selectedRun?.changedFilesCount || 0,
        hasDiff: selectedRun?.hasDiff || false,
        hasReport: selectedRun?.hasReport || false,
        lastEventType: selectedRun?.lastEventType || "",
      });

      if (eventsResult.status === "fulfilled") {
        const events = eventsResult.value.events || [];
        set({ replay: { ...get().replay, events, prompt, startedAt: formatTime(session?.created_at) || "" } });
        events.forEach((event) => {
          get().handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false, focusPanel: false });
        });
        set((s) => ({
          replay: { ...s.replay, index: events.length, status: events.length ? "ready" : "idle" },
        }));
        // Update run event count
        const currentRun = get().runs.find((r) => r.id === threadId);
        if (currentRun) {
          get().upsertRun({
            ...currentRun,
            eventCount: events.length,
            lastEventType: events.at(-1)?.type || currentRun.lastEventType,
          });
        }
      } else {
        get().addTimelineEvent({
          type: "error",
          title: "历史事件读取失败",
          content: eventsResult.reason?.message || "后端未返回事件列表。",
        });
      }

      await hydrateRunArtifacts(threadId, { refreshWorkspace: false });
      const updatedState = get();
      if (updatedState.report?.markdown) {
        set({ activeTab: "report" });
      } else if (updatedState.diff) {
        set({ activeTab: "diff" });
      }
      get().persistActiveSession();
      return true;
    } catch (error) {
      set({ status: "failed" });
      get().updateCurrentRunStatus("failed");
      get().addTimelineEvent({
        type: "error",
        title: "恢复历史运行失败",
        content: error.message,
      });
      return false;
    }
  }

  async function restoreConversation(conversationId, options = {}) {
    if (!conversationId) return false;
    const state = get();
    const quiet = Boolean(options.quiet);
    set({ activeTab: "report" });
    get().resetRunView("");
    if (!quiet) {
      get().addTimelineEvent({
        type: "conversation_restoring",
        title: "正在恢复会话",
        content: conversationId,
      });
    }

    try {
      const api = getApiClient();
      const query = state.workspaceDir ? `?workspace_dir=${encodeURIComponent(state.workspaceDir)}` : "";
      const conversation = await loadConversation({ fetchJson: api.fetchJson, conversationId, query });
      if (!conversation) throw new Error("后端未返回会话详情。");

      get().applyConversation(conversation, { reset: false });
      set({ currentConversationId: conversationId });
      get().persistActiveSession();
      const restoredMessages = mapConversationMessages(conversation);
      set({
        messages: restoredMessages.length ? restoredMessages : [{
          role: "assistant",
          author: "Lead Agent",
          time: formatTime(conversation.updated_at) || nowTime(),
          content: conversation.current_thread_id
            ? "会话已恢复。可以查看上次运行，也可以输入新需求继续让 nanoCursor 组队执行。"
            : "会话已恢复。输入需求后，我会基于这个会话团队直接启动运行。",
        }],
      });
      if (conversation.current_thread_id) {
        await restoreRun(conversation.current_thread_id, {
          force: true,
          quiet,
          replaceMessages: false,
        });
      }
      return true;
    } catch (error) {
      set({ status: "failed" });
      get().addTimelineEvent({
        type: "error",
        title: "恢复会话失败",
        content: error.message,
      });
      return false;
    }
  }

  async function runBenchmark(benchmarkId) {
    const state = get();
    const benchmark = (state.benchmarks || []).find((b) => b.id === benchmarkId);
    if (!benchmark) return;

    closeEventSourceSSE();
    set({
      status: "running",
      currentThreadId: "pending",
      activeTab: "artifacts",
      replay: { events: [], index: 0, speed: 1, status: "idle", prompt: "", startedAt: "" },
    });
    get().resetRunView(`运行基准任务：${benchmark.title}`);
    get().addMessage({
      role: "assistant",
      author: "Lead Agent",
      content: "我会按固定验收标准执行 Benchmark，并归档评分、测试、Diff 和交付物。",
    });

    try {
      const api = getApiClient();
      const run = await startBenchmarkApi({ requestJson: api.requestJson, benchmarkId, workspaceDir: state.workspaceDir });
      set({ currentThreadId: run.thread_id });
      get().persistActiveSession();
      set((s) => ({
        runs: [{
          id: run.thread_id,
          title: `Benchmark: ${run.title}`,
          status: "running",
          time: nowTime(),
        }, ...s.runs],
      }));
      get().addTimelineEvent({
        type: "run_started",
        title: "Benchmark 已启动",
        content: `${run.title} · ${run.thread_id}`,
      });
      await get().refreshWorkspaceData({ allowEmpty: false });
      connectEventsSSE(run.thread_id);
    } catch (error) {
      set({ status: "failed" });
      get().addTimelineEvent({
        type: "error",
        title: "Benchmark 启动失败",
        content: error.message,
      });
    }
  }

  async function cancelCurrentRun() {
    const state = get();
    const threadId = state.currentThreadId;
    if (!threadId || threadId === "pending") return;
    try {
      const api = getApiClient();
      await cancelRunApi({ requestJson: api.requestJson, threadId });
      set({ status: "cancelling" });
      get().updateCurrentRunStatus("cancelling");
      get().addTimelineEvent({
        type: "run_cancelling",
        title: "正在取消运行",
        content: "取消请求已发送，等待 Agent 在安全检查点停止。",
      });
      get().showToast({ kind: "success", title: "正在取消", content: shortId(threadId, "") });
    } catch (error) {
      get().showToast({ kind: "error", title: "取消失败", content: error.message });
    }
  }

  async function hydrateRunArtifacts(threadId, { refreshWorkspace = true } = {}) {
    const state = get();
    const api = getApiClient();

    if (refreshWorkspace) {
      await get().refreshWorkspaceData({ allowEmpty: true, includeRunState: false });
    }

    const bundle = await loadRunArtifactsBundleApi({
      fetchJson: api.fetchJson,
      threadId,
      includeArchived: Boolean(state.ephemeralAgents?.includeArchived),
    });

    // applyRunArtifactsBundle mutates a state object directly
    const tempState = {
      report: { ...state.report },
      runOutcome: state.runOutcome,
      diff: state.diff,
      diffFiles: state.diffFiles,
      metrics: { ...state.metrics },
      artifactCenter: { ...state.artifactCenter },
      recoveryCenter: { ...state.recoveryCenter },
      ephemeralAgents: { ...state.ephemeralAgents },
      currentRunStatus: state.status,
    };

    applyRunArtifactsBundle({
      state: tempState,
      bundle,
      threadId,
      setDiffState: (diff, changedFiles) => {
        tempState.diff = diff || "";
        tempState.diffFiles = parseUnifiedDiff(diff || "", changedFiles);
      },
    });

    set({
      report: tempState.report,
      runOutcome: tempState.runOutcome,
      diff: tempState.diff,
      diffFiles: tempState.diffFiles,
      metrics: tempState.metrics,
      artifactCenter: tempState.artifactCenter,
      recoveryCenter: tempState.recoveryCenter,
      ephemeralAgents: tempState.ephemeralAgents,
    });

    try {
      const events = await loadReplayEvents({ fetchJson: api.fetchJson, threadId });
      set((s) => ({ replay: { ...s.replay, events } }));
    } catch {
      // Replay is optional
    }
  }

  function applySnapshotToStore(snapshot, { replaceMessages = false } = {}) {
    const state = get();
    const tempState = {
      ...state,
      report: { ...state.report },
      runOutcome: state.runOutcome,
      diff: state.diff,
      diffFiles: state.diffFiles,
      metrics: { ...state.metrics },
      artifactCenter: { ...state.artifactCenter },
      recoveryCenter: { ...state.recoveryCenter },
      ephemeralAgents: { ...state.ephemeralAgents },
      team: Array.isArray(state.team) ? [...state.team] : [],
      messages: Array.isArray(state.messages) ? [...state.messages] : [],
      events: Array.isArray(state.events) ? [...state.events] : [],
      replay: { ...state.replay },
      approval: { ...state.approval },
    };

    applyRunSnapshot({
      state: tempState,
      snapshot,
      replaceMessages,
      setDiffState: (diff, changedFiles) => {
        tempState.diff = diff || "";
        tempState.diffFiles = parseUnifiedDiff(diff || "", changedFiles);
      },
    });

    set({
      runSnapshot: tempState.runSnapshot,
      status: tempState.status,
      currentRunStatus: tempState.currentRunStatus,
      currentThreadId: tempState.currentThreadId,
      currentConversationId: tempState.currentConversationId,
      workspaceDir: tempState.workspaceDir,
      workspaceInput: tempState.workspaceInput,
      messages: tempState.messages,
      events: tempState.events,
      replay: tempState.replay,
      tasks: tempState.tasks,
      metrics: tempState.metrics,
      report: tempState.report,
      runOutcome: tempState.runOutcome,
      diff: tempState.diff,
      diffFiles: tempState.diffFiles,
      artifactCenter: tempState.artifactCenter,
      recoveryCenter: tempState.recoveryCenter,
      agentActivities: tempState.agentActivities,
      ephemeralAgents: tempState.ephemeralAgents,
      team: tempState.team,
      approval: tempState.approval,
      selectedDiffFile: tempState.diffFiles?.some((file) => file.path === state.selectedDiffFile)
        ? state.selectedDiffFile
        : tempState.diffFiles?.[0]?.path || "",
    });
    get().persistActiveSession({
      workspaceDir: tempState.workspaceDir || state.workspaceDir,
      conversationId: tempState.currentConversationId,
      threadId: tempState.currentThreadId,
    });
  }

  async function refreshRunSnapshot(threadId = get().currentThreadId, { focusPanel = false, replaceMessages = false } = {}) {
    if (!threadId || threadId === "pending") return null;
    const api = getApiClient();
    try {
      const snapshot = await loadRunSnapshotApi({ fetchJson: api.fetchJson, threadId });
      applySnapshotToStore(snapshot, { replaceMessages });
      if (focusPanel && Array.isArray(snapshot.tasks) && snapshot.tasks.length) {
        set({ rightTab: "progress" });
      }
      return snapshot;
    } catch (error) {
      get().addTimelineEvent({
        type: "error",
        title: "运行快照读取失败",
        content: error.message,
      });
      return refreshRunState(threadId, { focusPanel });
    }
  }

  async function refreshRunState(threadId = get().currentThreadId, { focusPanel = false } = {}) {
    if (!threadId || threadId === "pending") return null;
    const api = getApiClient();
    try {
      const runState = await loadRunStateApi({ fetchJson: api.fetchJson, threadId });
      const tasks = Array.isArray(runState?.tasks) ? runState.tasks : [];
      const mapped = tasks.map((task) => get().normalizeTask?.(task) || null).filter(Boolean);
      if (mapped.length) {
        set((state) => {
          const existing = new Map(state.tasks.map((task) => [task.id, task]));
          for (const task of mapped) {
            existing.set(task.id, { ...(existing.get(task.id) || {}), ...task });
          }
          return {
            tasks: Array.from(existing.values()),
            metrics: { ...state.metrics, tasks: mapped.length },
            rightTab: focusPanel ? "tasks" : state.rightTab,
          };
        });
      }
      return runState;
    } catch (error) {
      get().addTimelineEvent({
        type: "error",
        title: "任务状态读取失败",
        content: error.message,
      });
      return null;
    }
  }

  return {
    runPrompt,
    restoreRun,
    restoreConversation,
    runBenchmark,
    cancelCurrentRun,
    buildConversationHistory,
    hydrateRunArtifacts,
    refreshRunState,
    refreshRunSnapshot,
  };
}

// SSE bridge helpers — imported from useSSE via global registration
let _connectEventsSSE = null;
let _closeEventSourceSSE = null;

export function registerSSEFunctions({ connectEvents, closeEventSource }) {
  _connectEventsSSE = connectEvents;
  _closeEventSourceSSE = closeEventSource;
}

function connectEventsSSE(threadId) {
  if (_connectEventsSSE) _connectEventsSSE(threadId);
}

function closeEventSourceSSE() {
  if (_closeEventSourceSSE) _closeEventSourceSSE();
}
