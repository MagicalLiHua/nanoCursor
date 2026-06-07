import { useEffect, useRef } from "react";
import useStore from "../store/index.js";
import { getApiClient } from "../core/sharedApi.js";
import { parseUnifiedDiff } from "../core/diff.js";
import { loadRunSession, loadReplayEvents, loadRunArtifactsBundle, loadRunSnapshot } from "../actions/runActions.js";
import { applyRunArtifactsBundle, applyRunSnapshot } from "../hydrators/runHydrator.js";
import { registerSSEFunctions } from "../store/actions/runActions.js";

const SSE_EVENT_TYPES = [
  "run_started",
  "agent_activity",
  "agent_complexity_assessed",
  "assistant_message",
  "plan_created",
  "approval_requested",
  "approval_resolved",
  "run_cancelling",
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
  "ephemeral_agent_updated",
  "agent_run_started",
  "agent_result_merged",
  "agent_run_failed",
  "agent_spawn_requested",
  "agent_spawn_approved",
  "agent_spawn_rejected",
  "parallel_agents_started",
  "parallel_agents_completed",
  "parallel_agent_progress",
  "parallel_agent_result",
  "parallel_agent_failed",
  "parallel_proposals_reviewed",
  "done",
  "error",
];

const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function useSSE() {
  const eventSourceRef = useRef(null);
  const reconciliationTimerRef = useRef(null);
  const reconciliationBusyRef = useRef(false);

  useEffect(() => {
    registerSSEFunctions({ connectEvents, closeEventSource });
    return () => {
      closeEventSource();
    };
  }, []);

  function connectEvents(threadId) {
    closeEventSource();

    const apiClient = getApiClient();
    const url = apiClient.eventSourceUrl(`/api/runs/${encodeURIComponent(threadId)}/events`);
    const es = new EventSource(url);
    eventSourceRef.current = es;
    startStatusReconciliation(threadId, apiClient, es);

    function handleParsedEvent(data) {
      useStore.getState().handleAgentEvent(data, {
        onDone: () => {
          stopStatusReconciliation();
          es.close();
          eventSourceRef.current = null;
          hydrateAfterDone(threadId, apiClient, data.payload?.status || "completed");
        },
        onError: () => {
          stopStatusReconciliation();
          es.close();
          eventSourceRef.current = null;
        },
      });
    }

    es.onmessage = (event) => {
      if (event.data?.trim()) {
        try { handleParsedEvent(JSON.parse(event.data)); } catch { /* ignore */ }
      }
    };

    SSE_EVENT_TYPES.forEach((type) => {
      es.addEventListener(type, (event) => {
        try { handleParsedEvent(JSON.parse(event.data)); } catch { /* ignore */ }
      });
    });

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      if (!["running", "waiting_approval", "cancelling"].includes(useStore.getState().status)) {
        stopStatusReconciliation();
        return;
      }

      loadRunSession({ fetchJson: apiClient.fetchJson, threadId })
        .then(async (session) => {
          const sessionStatus = session.status || "running";
          if (TERMINAL_RUN_STATUSES.has(sessionStatus)) {
            stopStatusReconciliation();
            await hydrateAfterDone(threadId, apiClient, sessionStatus);
            return;
          }
          useStore.getState().addTimelineEvent({
            type: "metrics_updated",
            title: "事件流已断开",
            content: "后端运行记录仍存在，可通过同步或历史运行恢复状态。",
          });
        })
        .catch((error) => {
          stopStatusReconciliation();
          useStore.getState().addTimelineEvent({
            type: "error",
            title: "事件流连接失败",
            content: `无法确认 run 状态：${error.message}`,
          });
          useStore.setState({ status: "failed" });
          useStore.getState().updateCurrentRunStatus("failed");
        });
    };
  }

  function startStatusReconciliation(threadId, apiClient, es) {
    stopStatusReconciliation();
    reconciliationTimerRef.current = setInterval(async () => {
      const state = useStore.getState();
      if (state.currentThreadId !== threadId || !["running", "waiting_approval", "cancelling"].includes(state.status)) {
        stopStatusReconciliation();
        return;
      }
      if (reconciliationBusyRef.current) return;

      reconciliationBusyRef.current = true;
      try {
        const session = await loadRunSession({ fetchJson: apiClient.fetchJson, threadId });
        const sessionStatus = session.status || "running";
        if (!TERMINAL_RUN_STATUSES.has(sessionStatus)) return;

        stopStatusReconciliation();
        es.close();
        if (eventSourceRef.current === es) eventSourceRef.current = null;
        await hydrateAfterDone(threadId, apiClient, sessionStatus);
      } catch {
        // SSE remains the primary transport; the next reconciliation tick can retry.
      } finally {
        reconciliationBusyRef.current = false;
      }
    }, 2000);
  }

  function stopStatusReconciliation() {
    if (reconciliationTimerRef.current) {
      clearInterval(reconciliationTimerRef.current);
      reconciliationTimerRef.current = null;
    }
  }

  function closeEventSource() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    stopStatusReconciliation();
  }

  return { connectEvents, closeEventSource };
}

function applyConfirmedTerminalStatus(status) {
  if (!TERMINAL_RUN_STATUSES.has(status)) return;
  useStore.setState({ status });
  useStore.getState().settleTasksForRunStatus(status);
  useStore.getState().updateCurrentRunStatus(status);
}

async function hydrateAfterDone(threadId, apiClient, confirmedStatus = "") {
  try {
    const store = useStore.getState();
    const snapshot = await loadRunSnapshot({ fetchJson: apiClient.fetchJson, threadId });
    const tempState = {
      ...store,
      report: { ...store.report },
      runOutcome: store.runOutcome,
      diff: store.diff,
      diffFiles: store.diffFiles,
      metrics: { ...store.metrics },
      artifactCenter: { ...store.artifactCenter },
      recoveryCenter: { ...store.recoveryCenter },
      ephemeralAgents: { ...store.ephemeralAgents },
      team: Array.isArray(store.team) ? [...store.team] : [],
      messages: Array.isArray(store.messages) ? [...store.messages] : [],
      events: Array.isArray(store.events) ? [...store.events] : [],
      replay: { ...store.replay },
      approval: { ...store.approval },
    };

    applyRunSnapshot({
      state: tempState,
      snapshot,
      replaceMessages: false,
      setDiffState: (diff, changedFiles) => {
        tempState.diff = diff || "";
        tempState.diffFiles = parseUnifiedDiff(diff || "", changedFiles);
      },
    });

    useStore.setState({
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
      selectedDiffFile: tempState.diffFiles?.[0]?.path || "",
    });
    applyConfirmedTerminalStatus(confirmedStatus);
    return;
  } catch {
    // Fall back to legacy artifact hydration below.
  }

  try {
    const store = useStore.getState();
    const bundle = await loadRunArtifactsBundle({
      fetchJson: apiClient.fetchJson,
      threadId,
      includeArchived: Boolean(store.ephemeralAgents?.includeArchived),
    });

    // applyRunArtifactsBundle mutates a state object directly,
    // so we create a temp copy and sync back to Zustand after.
    const tempState = {
      report: { ...store.report },
      runOutcome: store.runOutcome,
      diff: store.diff,
      diffFiles: store.diffFiles,
      metrics: { ...store.metrics },
      artifactCenter: { ...store.artifactCenter },
      recoveryCenter: { ...store.recoveryCenter },
      ephemeralAgents: { ...store.ephemeralAgents },
      currentRunStatus: store.status,
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

    useStore.setState({
      report: tempState.report,
      runOutcome: tempState.runOutcome,
      diff: tempState.diff,
      diffFiles: tempState.diffFiles,
      metrics: tempState.metrics,
      artifactCenter: tempState.artifactCenter,
      recoveryCenter: tempState.recoveryCenter,
      ephemeralAgents: tempState.ephemeralAgents,
    });
    applyConfirmedTerminalStatus(confirmedStatus);

    try {
      const events = await loadReplayEvents({ fetchJson: apiClient.fetchJson, threadId });
      useStore.setState((s) => ({ replay: { ...s.replay, events } }));
    } catch {
      // Replay is optional
    }
  } catch {
    // Artifacts hydration is best-effort
  } finally {
    applyConfirmedTerminalStatus(confirmedStatus);
  }
}
