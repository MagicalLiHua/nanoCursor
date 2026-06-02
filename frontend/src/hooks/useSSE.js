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

export function useSSE() {
  const eventSourceRef = useRef(null);

  useEffect(() => {
    registerSSEFunctions({ connectEvents, closeEventSource });
    return () => {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
    };
  }, []);

  function connectEvents(threadId) {
    eventSourceRef.current?.close();

    const apiClient = getApiClient();
    const url = apiClient.eventSourceUrl(`/api/runs/${encodeURIComponent(threadId)}/events`);
    const es = new EventSource(url);
    eventSourceRef.current = es;

    function handleParsedEvent(data) {
      useStore.getState().handleAgentEvent(data, {
        onDone: () => {
          es.close();
          eventSourceRef.current = null;
          hydrateAfterDone(threadId, apiClient);
        },
        onError: () => {
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
      if (useStore.getState().status !== "running") return;

      loadRunSession({ fetchJson: apiClient.fetchJson, threadId })
        .then(async (session) => {
          const sessionStatus = session.status || "running";
          if (["completed", "failed", "cancelled"].includes(sessionStatus)) {
            useStore.setState({ status: sessionStatus });
            useStore.getState().settleTasksForRunStatus(sessionStatus);
            useStore.getState().updateCurrentRunStatus(sessionStatus);
            await hydrateAfterDone(threadId, apiClient);
            return;
          }
          useStore.getState().addTimelineEvent({
            type: "metrics_updated",
            title: "事件流已断开",
            content: "后端运行记录仍存在，可通过同步或历史运行恢复状态。",
          });
        })
        .catch((error) => {
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

  function closeEventSource() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  }

  return { connectEvents, closeEventSource };
}

async function hydrateAfterDone(threadId, apiClient) {
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
      replaceMessages: true,
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

    try {
      const events = await loadReplayEvents({ fetchJson: apiClient.fetchJson, threadId });
      useStore.setState((s) => ({ replay: { ...s.replay, events } }));
    } catch {
      // Replay is optional
    }
  } catch {
    // Artifacts hydration is best-effort
  }
}
