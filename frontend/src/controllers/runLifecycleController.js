import {
  loadRunSessionAndEvents,
  startBenchmark as startBenchmarkAction,
  startRun as startRunAction,
} from "../actions/runActions.js";
import { loadConversation } from "../actions/teamActions.js";

export function createRunLifecycleController({
  state,
  fetchJson,
  requestJson,
  apiCandidates,
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
}) {
  async function restoreRun(threadId, options = {}) {
    if (!threadId) return;
    const force = Boolean(options.force);
    closeEventSource();
    stopReplayTimer();

    const selectedRun = state.runs.find((run) => run.id === threadId);
    if (selectedRun?.kind === "conversation") {
      await restoreConversation(selectedRun.conversationId || selectedRun.id, { force });
      return;
    }
    if (threadId === state.currentThreadId && !force) {
      return;
    }
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
      const { sessionResult, eventsResult } = await loadRunSessionAndEvents({ fetchJson, threadId });

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

  async function restoreConversation(conversationId, options = {}) {
    if (!conversationId) return;
    const force = Boolean(options.force);
    state.activeTab = "timeline";
    resetRunView("");
    addTimelineEvent({
      type: "conversation_restoring",
      title: "正在恢复会话",
      content: conversationId,
    });

    try {
      const conversation = await loadConversation({ fetchJson, conversationId, query: conversationQuery() });
      if (!conversation) {
        throw new Error("后端未返回会话详情。");
      }
      applyConversation(conversation, { reset: false });
      state.currentConversationId = conversationId;
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
        await restoreRun(conversation.current_thread_id, { force });
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

  async function runPrompt(prompt, options = {}) {
    closeEventSource();
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
      const result = await startRunAction({
        requestJson,
        conversationId: state.currentConversationId,
        prompt,
        workspaceDir: state.workspaceDir,
        demo: Boolean(options.demo),
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
        content: `${apiCandidates.join(" 或 ")} 未返回可用响应。`,
      });
      showToast("error", "运行启动失败", error.message);
    }
  }

  async function runBenchmark(benchmarkId) {
    const benchmark = state.benchmarks.find((item) => item.id === benchmarkId);
    if (!benchmark) return;
    closeEventSource();
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
      const run = await startBenchmarkAction({ requestJson, benchmarkId, workspaceDir: state.workspaceDir });
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

  return {
    restoreConversation,
    restoreRun,
    runBenchmark,
    runPrompt,
  };
}
