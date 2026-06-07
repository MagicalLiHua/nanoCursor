import { parseUnifiedDiff } from "../../core/diff.js";
import { getApiClient } from "../../core/sharedApi.js";
import { approvalDecisionLabel, agentToneFromName, nowTime as nowTimeFn } from "../../core/format.js";
import { mapTraceability } from "../../hydrators/runHydrator.js";
import { normalizeApprovalTasks } from "../../services/approvalService.js";
import { normalizeTask as normalizeTaskBase, mapBackendTeam as mapBackendTeamBase } from "../../state/mappers.js";
import { inferTaskCapabilities } from "../../state/selectors.js";
import { blankReport, blankArtifactCenter, blankRecoveryCenter, blankEphemeralAgents } from "../../state/runDefaults.js";
import { updateAgentActivityQueue } from "../../state/chatState.js";

function normalizeAgentName(agent, payload = {}) {
  const hinted = payload?.capability_trace?.agent || agent || "Lead";
  const text = String(hinted).trim();
  if (!text) return "Lead";
  const clean = text.endsWith("Agent") ? text.replace(/\s*Agent$/, "") : text;
  const known = {
    lead: "Lead", planner: "Planner", coder: "Coder", tester: "Tester",
    reviewer: "Reviewer", designer: "Designer", devops: "DevOps",
    security: "Security", migration: "Migration", system: "System",
  };
  return known[clean.toLowerCase()] || clean;
}

function isExplicitAgentWorkEvent(eventType, payload = {}) {
  if (eventType === "agent_activity") return true;
  if (["parallel_agent_progress", "parallel_agent_result", "parallel_agent_failed"].includes(eventType)) return true;
  if (["agent_run_started", "agent_result_merged", "agent_run_failed"].includes(eventType)) return true;
  if (eventType === "ephemeral_agent_updated") {
    return ["working", "completed"].includes(String(payload?.status || "").toLowerCase());
  }
  if (eventType === "ephemeral_agent_completed") return true;
  // Agent pool status events
  if (eventType.startsWith("agent_") && payload?.event) return true;
  return false;
}

function activityText({ title, content, eventType, payload }) {
  if (eventType === "agent_activity") {
    const phase = payload?.phase ? ` · ${payload.phase}` : "";
    return `${content || title || "Agent 正在工作"}${phase}`.slice(0, 220);
  }
  if (eventType === "agent_complexity_assessed") {
    const level = payload?.complexity?.level || "";
    const names = Array.isArray(payload?.members)
      ? payload.members.map((m) => m.name || m.role).filter(Boolean).slice(0, 6).join(" / ")
      : "";
    return `复杂度 ${level || "unknown"}，本轮团队：${names || "Lead"}`;
  }
  if (eventType === "agent_spawn_requested") return content || "Lead 正在请求创建临时 Agent。";
  if (eventType === "agent_spawn_approved") return content || "临时 Agent 已创建。";
  if (eventType === "agent_spawn_rejected") return content || "临时 Agent 创建被拒绝。";
  if (eventType === "ephemeral_agent_spawned") return `${payload?.name || "临时 Agent"} 已加入本轮任务。`;
  if (eventType === "ephemeral_agent_completed") return `${payload?.name || "临时 Agent"} 已完成并准备归档。`;
  if (eventType === "ephemeral_agent_archived") return `${payload?.name || "临时 Agent"} 已自动归档。`;
  if (eventType === "tool_call_finished") {
    const tool = payload?.tool || payload?.capability_trace?.capability_name || "tool";
    const preview = String(content || payload?.output || "").split("\n").find(Boolean) || "工具调用完成。";
    return `调用 ${tool}: ${preview}`.slice(0, 180);
  }
  if (eventType === "stage_updated") {
    const status = payload?.status ? ` -> ${payload.status}` : "";
    return `${payload?.title || title}${status}`;
  }
  if (eventType === "task_created") return `创建任务：${payload?.task?.title || title}`;
  if (eventType === "task_updated") return `更新任务状态：${payload?.status || title}`;
  if (eventType === "approval_resolved") return "工具审批已处理，继续执行。";
  if (eventType === "tool_approval_required" || eventType === "run_waiting_approval") {
    return content || "等待用户审批工具调用。";
  }
  if (eventType === "assistant_message") return "正在整理回复和交付说明。";
  if (eventType === "done") return "本轮运行完成。";
  if (eventType === "run_cancelling") return "正在取消运行，等待安全停止点。";
  if (eventType === "error") return content || "运行遇到错误。";
  if (eventType === "team_updated") return "已更新本轮 Agent 团队。";
  if (eventType === "plan_created") return "已生成本轮执行策略。";
  return content || title;
}

const nowTime = nowTimeFn;

function normalizeTask(task) {
  return normalizeTaskBase(task, { inferTaskCapabilities });
}

function mapBackendTeam(members) {
  return mapBackendTeamBase(members, { agentToneFromName });
}

function eventIndicatesLeadDirect(eventType, payload = {}, state = {}) {
  const snapshotStrategy = state.runSnapshot?.run?.strategy || "";
  const currentStrategy = state.currentRunStrategy || snapshotStrategy;
  const intentRoute = payload?.intent_decision?.execution_route ||
    payload?.complexity?.execution_route ||
    payload?.complexity?.intent_decision?.execution_route;
  return currentStrategy === "lead_direct_reply" ||
    payload?.strategy === "lead_direct_reply" ||
    intentRoute === "lead_direct_reply" ||
    (eventType === "plan_created" && payload?.strategy === "lead_direct_reply");
}

function blankApproval() {
  return { kind: "", status: "idle", planId: "", title: "", content: "", riskLevel: "", tasks: [], decision: "", comment: "" };
}

export function createEventActions(set, get) {
  function addTimelineEvent(event) {
    set((state) => ({
      events: [...state.events, { ...event, time: event.time || nowTime() }],
    }));
  }

  function addMessage(message) {
    set((state) => ({
      messages: [...state.messages, { ...message, time: message.time || nowTime() }],
    }));
  }

  function upsertTask(task) {
    const normalized = normalizeTask(task);
    if (!normalized) return;
    set((state) => {
      const idx = state.tasks.findIndex((t) => t.id === normalized.id);
      if (idx >= 0) {
        const next = [...state.tasks];
        next[idx] = { ...next[idx], ...normalized };
        return { tasks: next };
      }
      return { tasks: [...state.tasks, normalized] };
    });
  }

  function patchTask(taskId, patch) {
    set((state) => ({
      tasks: state.tasks.map((t) => (t.id === taskId ? { ...t, ...patch } : t)),
    }));
  }

  function patchStageTask(payload) {
    if (!payload?.stage_id) return;
    const taskId = `stage-1-${payload.stage_id}`;
    set((state) => {
      const idx = state.tasks.findIndex((t) => t.id === taskId);
      if (idx >= 0) {
        const next = [...state.tasks];
        next[idx] = {
          ...next[idx],
          status: payload.status || next[idx].status,
          title: payload.title || next[idx].title,
          description: payload.description || next[idx].description,
          owner: payload.owner || next[idx].owner,
        };
        return { tasks: next };
      }
      return {
        tasks: [...state.tasks, normalizeTask({
          id: taskId,
          title: payload.title || payload.stage_id,
          description: payload.description || "",
          status: payload.status || "pending",
          owner: payload.owner || "Agent",
        })].filter(Boolean),
      };
    });
  }

  function attachToolEvidenceToTask(stageId, evidence) {
    const taskId = `stage-1-${stageId}`;
    set((state) => ({
      tasks: state.tasks.map((t) => {
        if (t.id !== taskId) return t;
        const toolEvidence = Array.isArray(t.toolEvidence) ? [...t.toolEvidence, evidence] : [evidence];
        return { ...t, toolEvidence };
      }),
    }));
  }

  function upsertFile(path) {
    if (!path) return;
    set((state) => {
      if (state.files.includes(path)) return {};
      return { files: [...state.files, path] };
    });
  }

  function setDiffState(diff, changedFiles = []) {
    const diffFiles = parseUnifiedDiff(diff || "", changedFiles);
    set((state) => {
      const selectedDiffFile = diffFiles.some((f) => f.path === state.selectedDiffFile)
        ? state.selectedDiffFile
        : diffFiles[0]?.path || "";
      return { diff: diff || "", diffFiles, selectedDiffFile };
    });
  }

  function setTraceability(traceability) {
    const mapped = mapTraceability(traceability);
    set((state) => ({
      report: {
        ...state.report,
        traceability: mapped,
        requirements: mapped.requirements.map((item) => `${item.id}: ${item.title}`),
      },
    }));
  }

  function upsertEphemeralAgent(agent) {
    set((state) => {
      const prev = state.ephemeralAgents || blankEphemeralAgents();
      const agents = Array.isArray(prev.agents) ? [...prev.agents] : [];
      const idx = agents.findIndex((a) => a.agent_id === agent.agent_id);
      if (idx >= 0) {
        agents[idx] = { ...agents[idx], ...agent };
      } else {
        agents.push(agent);
      }
      const activeCount = agents.filter((a) => !["archived", "expired"].includes(a.status)).length;
      return {
        ephemeralAgents: {
          ...prev,
          agents,
          active_count: activeCount,
          total: agents.length,
        },
      };
    });
  }

  function settleTasksForRunStatus(status) {
    set((state) => {
      if (!["completed", "failed", "cancelled"].includes(status)) return {};
      if (state.currentRunStrategy === "lead_direct_reply" || state.runSnapshot?.run?.strategy === "lead_direct_reply") {
        return { tasks: [], metrics: { ...state.metrics, tasks: 0 } };
      }
      return {
        tasks: state.tasks.map((t) => {
          if (t.status === "completed" || t.status === "failed") return t;
          return { ...t, status: status === "completed" ? "completed" : "failed" };
        }),
      };
    });
  }

  function updateCurrentRunStatus(status) {
    set((state) => {
      const runs = state.runs.map((run) => {
        if (run.id === state.currentThreadId) return { ...run, status };
        if (run.id === state.currentConversationId) {
          return { ...run, status, threadId: state.currentThreadId, time: nowTime() };
        }
        return run;
      });
      return { runs };
    });
  }

  function recordAgentActivity({ agent, title, content, time, eventType, payload }) {
    const normalizedAgent = normalizeAgentName(agent, payload);
    const text = activityText({ title, content, eventType, payload });
    if (!text) return;

    const inputTokens = Number(payload?.input_tokens) || 0;
    const outputTokens = Number(payload?.output_tokens) || 0;

    set((state) => {
      // Token counts
      let agentTokenCounts = state.agentTokenCounts;
      if (inputTokens || outputTokens) {
        const tokenKey = normalizedAgent.toLowerCase();
        const prev = agentTokenCounts[tokenKey] || { input: 0, output: 0 };
        agentTokenCounts = {
          ...agentTokenCounts,
          [tokenKey]: {
            input: Math.max(prev.input, inputTokens),
            output: Math.max(prev.output, outputTokens),
          },
        };
      }

      // Activity list
      const next = {
        agent: normalizedAgent,
        title,
        text,
        eventType,
        time,
        payload,
        explicitAgentWork: isExplicitAgentWorkEvent(eventType, payload),
        inputTokens,
        outputTokens,
      };
      const agentActivities = updateAgentActivityQueue(state.agentActivities, next);

      // Update team member status
      const team = state.team.map((member) => {
        const memberName = normalizeAgentName(member.name || member.role);
        if (memberName === normalizedAgent) {
          return {
            ...member,
            status: state.status === "running" ? "running" : member.status || "idle",
            lastAction: text,
          };
        }
        return member;
      });

      return { agentTokenCounts, agentActivities, team };
    });
  }

  // --- Main event handler ---
  function handleAgentEvent(event, options = {}) {
    const state = get();

    // Dedup
    if (event.id) {
      const seen = Array.isArray(state._seenEventIds) ? state._seenEventIds : [];
      if (seen.includes(event.id)) {
        return;
      }
      set({ _seenEventIds: [...seen, event.id] });
    }

    const eventType = event.type || "message";
    const title = event.title || eventType;
    const content = event.content || "";
    const payload = event.payload || {};
    const isLeadDirectRun = eventIndicatesLeadDirect(eventType, payload, state);
    const time = event.timestamp
      ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(event.timestamp * 1000))
      : nowTime();

    // Token events: accumulate in streaming buffer, skip timeline
    if (eventType === "token") {
      const delta = event.payload?.delta || content || "";
      set((s) => ({ streamingContent: (s.streamingContent || "") + delta }));
      return;
    }

    // Push to events timeline
    set((s) => ({
      events: [...s.events, {
        type: eventType,
        title,
        content,
        time,
        agent: event.agent || "",
        payload: event.payload || {},
      }],
    }));

    if (eventType === "plan_created" && payload?.strategy) {
      set({ currentRunStrategy: payload.strategy });
    } else if (isLeadDirectRun && !get().currentRunStrategy) {
      set({ currentRunStrategy: "lead_direct_reply" });
    }

    // Record agent activity
    recordAgentActivity({
      agent: event.agent || event.payload?.capability_trace?.agent || "Lead",
      title,
      content,
      time,
      eventType,
      payload,
    });

    const shouldFocusPanel = options.focusPanel !== false;

    // --- Event type handlers ---

    if (eventType === "assistant_message") {
      set({ streamingContent: "" });
      addMessage({
        role: "assistant",
        author: `${event.agent || "Lead"} Agent`,
        content,
        time,
      });
    }

    if (eventType === "plan_created" && payload?.tasks && !isLeadDirectRun) {
      payload.tasks.forEach((task) => upsertTask(task));
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    if (!isLeadDirectRun && (["run_state_created", "run_state_patched", "task_started"].includes(eventType) || eventType.startsWith("task_"))) {
      get().refreshRunState?.(get().currentThreadId, { focusPanel: shouldFocusPanel });
    }

    if (eventType === "agent_complexity_assessed" && event.payload?.members) {
      set({ team: mapBackendTeam(event.payload.members) });
    }

    if (eventType === "stage_updated" && event.payload?.stage_id && !isLeadDirectRun) {
      patchStageTask(event.payload);
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    if (eventType === "approval_requested") {
      set({
        approval: {
          kind: "plan",
          status: "pending",
          planId: event.payload?.plan_id || "default-plan",
          title,
          content,
          riskLevel: event.payload?.risk_level || "",
          tasks: normalizeApprovalTasks(event.payload?.tasks),
          decision: "",
          comment: "",
        },
        activeTab: "timeline",
      });
    }

    if (eventType === "tool_approval_required" || eventType === "run_waiting_approval") {
      const decision = event.payload?.decision || {};
      set({
        approval: {
          kind: "tool",
          status: "pending",
          decisionId: decision.decision_id || "",
          tool: decision.tool || event.payload?.tool || "",
          title: title || "工具需要审批",
          content: content || decision.reason || "",
          riskLevel: decision.risk_level || "",
          tasks: normalizeApprovalTasks([{
            id: decision.decision_id || decision.tool || "tool",
            title: `${decision.tool || event.payload?.tool || "工具"}：${decision.reason || content || "等待审批"}`,
            status: "pending",
          }]),
          decision: "",
          comment: "",
        },
        activeTab: "timeline",
      });
    }

    if (eventType === "approval_resolved") {
      set({ approval: blankApproval() });
    }

    if (eventType === "run_cancelling") {
      set({ status: "cancelling" });
      updateCurrentRunStatus("cancelling");
    }

    if (eventType === "tool_call_finished") {
      set((s) => ({ metrics: { ...s.metrics, toolCalls: s.metrics.toolCalls + 1 } }));
      if (event.payload?.stage_id) {
        attachToolEvidenceToTask(event.payload.stage_id, {
          tool: event.payload.tool || title,
          capabilityId: event.payload.capability_trace?.capability_id || "",
          capabilityName: event.payload.capability_trace?.capability_name || "",
          agent: event.payload.capability_trace?.agent || event.agent || "",
          ok: !String(event.payload.output || content || "").startsWith("Error:"),
          time,
        });
      }
    }

    if (eventType === "task_created" && event.payload?.task && !isLeadDirectRun) {
      upsertTask(event.payload.task);
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    if (eventType === "task_updated" && event.payload?.task_id && !isLeadDirectRun) {
      patchTask(event.payload.task_id, {
        status: event.payload.status,
        title: event.payload.title,
        description: event.payload.description,
        owner: event.payload.owner,
        capabilities: event.payload.capabilities,
      });
      set({ rightTab: "progress" });
    }

    if (eventType === "team_updated" && event.payload?.members) {
      set({ team: mapBackendTeam(event.payload.members) });
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    if (eventType.startsWith("ephemeral_agent_") && event.payload?.agent_id) {
      upsertEphemeralAgent({
        agent_id: event.payload.agent_id,
        name: event.payload.name,
        role: event.payload.role,
        status: event.payload.status || (eventType === "ephemeral_agent_spawned" ? "active" : "archived"),
        goal: event.payload.goal,
        reason: event.payload.reason,
        capabilities: event.payload.capabilities || [],
        mcp_servers: event.payload.mcp_servers || [],
        result: event.payload.result || {},
        terminal_status:
          eventType === "ephemeral_agent_completed" ? "completed"
            : eventType === "ephemeral_agent_expired" ? "expired" : "",
      });
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    if (["agent_run_started", "agent_result_merged", "agent_run_failed"].includes(eventType) && event.payload?.agent_id) {
      const agentPayload = event.payload.agent || {};
      upsertEphemeralAgent({
        agent_id: event.payload.agent_id,
        name: agentPayload.name || event.agent || "Temporary Agent",
        role: agentPayload.role || "",
        status: agentPayload.status || (eventType === "agent_run_started" ? "working" : "archived"),
        goal: agentPayload.goal || content || "",
        reason: agentPayload.reason || "",
        capabilities: agentPayload.capabilities || [],
        mcp_servers: agentPayload.mcp_servers || [],
        result: event.payload.result || agentPayload.result || {},
        archive_reason: event.payload.error || agentPayload.archive_reason || "",
        terminal_status:
          eventType === "agent_result_merged" ? "completed"
            : eventType === "agent_run_failed" ? "failed" : "",
      });
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    // Agent pool status events (agent_started, agent_completed, agent_failed, agent_cancelled)
    if (eventType.startsWith("agent_") && event.payload?.agent_id && event.payload?.event) {
      const poolEvent = event.payload.event;
      const poolStatus = poolEvent === "started" ? "running"
        : poolEvent === "completed" ? "completed"
        : poolEvent === "failed" ? "failed"
        : poolEvent === "cancelled" ? "cancelled" : "unknown";
      upsertEphemeralAgent({
        agent_id: event.payload.agent_id,
        name: event.payload.name || event.agent || "Sub-Agent",
        role: event.payload.role || "",
        status: poolStatus,
        result: event.payload.result || event.payload.error || "",
      });
      if (shouldFocusPanel) set({ rightTab: "progress" });
    }

    if (eventType === "file_changed" && event.payload?.path) {
      upsertFile(event.payload.path);
    }

    if (eventType === "diff_updated" && event.payload) {
      if (typeof event.payload.diff === "string") {
        setDiffState(
          event.payload.diff || "Diff is empty. The file may be new, unchanged, or outside git tracking.",
          event.payload.changed_files || get().report.changedFiles,
        );
      }
      if (Array.isArray(event.payload.changed_files)) {
        event.payload.changed_files.forEach((file) => upsertFile(typeof file === "string" ? file : file.path));
        if (event.payload.changed_files.length) {
          set((s) => ({
            report: {
              ...s.report,
              changedFiles: event.payload.changed_files.map((file) => typeof file === "string" ? file : file.path),
            },
          }));
        }
      }
      set({ activeTab: "diff" });
    }

    if (eventType === "metrics_updated" && event.payload) {
      set((s) => ({
        metrics: { ...s.metrics, tokens: event.payload.total_tokens || s.metrics.tokens },
      }));
    }

    if (eventType === "test_finished" && event.payload?.status) {
      set((s) => ({
        metrics: { ...s.metrics, tests: event.payload.status === "passed" ? "passed" : event.payload.status },
      }));
    }

    if (eventType === "preview_started" && event.payload?.preview_url) {
      set({ previewUrl: event.payload.preview_url, activeTab: "preview" });
    }

    if (eventType === "report_ready" && event.payload?.markdown) {
      set((s) => ({
        report: {
          ...s.report,
          markdown: event.payload.markdown,
          changedFiles: Array.isArray(event.payload.changed_files)
            ? event.payload.changed_files.map((file) => file.path || file)
            : s.report.changedFiles,
        },
        activeTab: "report",
      }));
    }

    if (eventType === "traceability_ready" && event.payload?.requirements) {
      setTraceability({
        source: "event",
        coverage_rate: event.payload.coverage_rate || 0,
        total_count: event.payload.requirements.length,
        covered_count: event.payload.requirements.filter((item) => item.status === "covered").length,
        partial_count: event.payload.requirements.filter((item) => item.status === "partial").length,
        missing_count: event.payload.requirements.filter((item) => item.status === "missing").length,
        requirements: event.payload.requirements,
      });
      set({ activeTab: "report" });
    }

    if (eventType === "done") {
      const finalStatus = event.payload?.status || "completed";
      set({ status: finalStatus, showCompletedTasks: false });
      settleTasksForRunStatus(finalStatus);
      updateCurrentRunStatus(finalStatus);
      if (options.onDone) options.onDone();
    }

    if (eventType === "error") {
      set({ status: "failed" });
      updateCurrentRunStatus("failed");
      if (options.onError) options.onError();
    }
  }

  function resetRunView(prompt) {
    set({
      events: [],
      streamingContent: "",
      messages: prompt
        ? [{ role: "user", author: "用户", time: nowTime(), content: prompt }]
        : [],
      tasks: [],
      files: [],
      metrics: {
        tasks: 0,
        files: 0,
        toolCalls: 0,
        tokens: "--",
        tests: "--",
      },
      diff: "",
      diffFiles: [],
      selectedDiffFile: "",
      report: blankReport(),
      artifactCenter: blankArtifactCenter("idle"),
      recoveryCenter: blankRecoveryCenter("safe"),
      runSnapshot: null,
      runOutcome: null,
      previewUrl: "",
      agentActivities: [],
      agentTokenCounts: {},
      currentRunStatus: "idle",
      currentRunStrategy: "",
      showCompletedTasks: false,
      approval: { status: "idle", planId: "", title: "", content: "", riskLevel: "", tasks: [], decision: "", comment: "" },
      approvalComment: "",
      replay: { events: [], index: 0, speed: 1, status: "idle", prompt: "", startedAt: "" },
    });
  }

  function resetSeenEventIds() {
    set({ _seenEventIds: [] });
  }

  async function submitApprovalDecision(decision) {
    const state = get();
    if (!decision || state.approval?.status !== "pending") return;

    const comment = state.approvalComment || "";
    const planId = state.approval.planId || "default-plan";
    const isToolApproval = state.approval.kind === "tool";

    set({ approval: { ...state.approval, status: "submitting", decision, comment } });

    const apiClient = getApiClient();

    try {
      if (isToolApproval) {
        const decisionId = state.approval.decisionId;
        const approvalThreadId = state.approval.threadId || state.currentThreadId;
        await apiClient.requestJson(
          `/api/runs/${encodeURIComponent(approvalThreadId)}/approvals/${encodeURIComponent(decisionId)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ approved: decision === "approved", comment }),
          },
        );
        handleAgentEvent({
          type: "approval_resolved",
          title: approvalDecisionLabel(decision),
          content: comment || `工具 ${state.approval.tool || ""} ${approvalDecisionLabel(decision)}。`,
          agent: "user",
          payload: { decision_id: decisionId, thread_id: approvalThreadId, decision, approved: decision === "approved", comment },
        });
      } else {
        const event = await apiClient.requestJson(
          `/api/runs/${encodeURIComponent(state.currentThreadId)}/approval`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision, plan_id: planId, comment }),
          },
        );
        handleAgentEvent(event);
      }
    } catch (error) {
      handleAgentEvent({
        type: "approval_resolved",
        title: approvalDecisionLabel(decision),
        content: comment || `本地已记录审批结果：${approvalDecisionLabel(decision)}。`,
        agent: "user",
        payload: { plan_id: planId, decision, comment, local_only: true },
      });
      addTimelineEvent({
        type: "error",
        title: "审批结果未能写入后端",
        content: error.message,
      });
    }
  }

  return {
    handleAgentEvent,
    addMessage,
    addTimelineEvent,
    upsertTask,
    patchTask,
    patchStageTask,
    attachToolEvidenceToTask,
    upsertFile,
    setDiffState,
    setTraceability,
    upsertEphemeralAgent,
    settleTasksForRunStatus,
    updateCurrentRunStatus,
    resetRunView,
    resetSeenEventIds,
    submitApprovalDecision,
  };
}
