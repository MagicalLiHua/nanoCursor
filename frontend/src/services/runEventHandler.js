export function handleAgentEvent(event, options = {}, context) {
  const {
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
  } = context;

  if (event.id) {
    if (seenEventIds.has(event.id)) return;
    seenEventIds.add(event.id);
  }
  const eventType = event.type || "message";
  const title = event.title || eventType;
  const content = event.content || "";
  const time = event.timestamp
    ? new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(event.timestamp * 1000))
    : nowTime();

  state.events.push({
    type: eventType,
    title,
    content,
    time,
    agent: event.agent || "",
    payload: event.payload || {},
  });

  if (eventType === "assistant_message") {
    addMessage({
      role: "assistant",
      author: `${event.agent || "Lead"} Agent`,
      content,
      time,
    });
  }

  const shouldFocusPanel = options.focusPanel !== false;

  if (eventType === "plan_created" && event.payload?.tasks) {
    event.payload.tasks.forEach((task) => upsertTask(task));
    if (shouldFocusPanel) state.rightTab = "tasks";
  }

  if (eventType === "stage_updated" && event.payload?.stage_id) {
    patchStageTask(event.payload);
    if (shouldFocusPanel) state.rightTab = "tasks";
  }

  if (eventType === "approval_requested") {
    state.approval = {
      kind: "plan",
      status: "pending",
      planId: event.payload?.plan_id || "default-plan",
      title,
      content,
      riskLevel: event.payload?.risk_level || "",
      tasks: normalizeApprovalTasks(event.payload?.tasks),
      decision: "",
      comment: "",
    };
    state.activeTab = "timeline";
  }

  if (eventType === "tool_approval_required" || eventType === "run_waiting_approval") {
    const decision = event.payload?.decision || {};
    state.approval = {
      kind: "tool",
      status: "pending",
      decisionId: decision.decision_id || "",
      tool: decision.tool || event.payload?.tool || "",
      title: title || "工具需要审批",
      content: content || decision.reason || "",
      riskLevel: decision.risk_level || "",
      tasks: normalizeApprovalTasks([
        {
          id: decision.decision_id || decision.tool || "tool",
          title: `${decision.tool || event.payload?.tool || "工具"}：${decision.reason || content || "等待审批"}`,
          status: "pending",
        },
      ]),
      decision: "",
      comment: "",
    };
    state.activeTab = "timeline";
  }

  if (eventType === "approval_resolved") {
    state.approval = blankApproval();
  }

  if (eventType === "tool_call_finished") {
    state.metrics.toolCalls += 1;
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

  if (eventType === "task_created" && event.payload?.task) {
    upsertTask(event.payload.task);
    if (shouldFocusPanel) state.rightTab = "tasks";
  }

  if (eventType === "task_updated" && event.payload?.task_id) {
    patchTask(event.payload.task_id, {
      status: event.payload.status,
      title: event.payload.title,
      description: event.payload.description,
      owner: event.payload.owner,
      capabilities: event.payload.capabilities,
    });
    state.rightTab = "tasks";
  }

  if (eventType === "team_updated" && event.payload?.members) {
    state.team = mapBackendTeam(event.payload.members);
    if (shouldFocusPanel) state.rightTab = "team";
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
      terminal_status:
        eventType === "ephemeral_agent_completed"
          ? "completed"
          : eventType === "ephemeral_agent_expired"
            ? "expired"
            : "",
    });
    if (shouldFocusPanel) state.rightTab = "ephemeral";
  }

  if (eventType === "file_changed" && event.payload?.path) {
    upsertFile(event.payload.path);
  }

  if (eventType === "diff_updated" && event.payload) {
    if (typeof event.payload.diff === "string") {
      setDiffState(
        event.payload.diff || "Diff is empty. The file may be new, unchanged, or outside git tracking.",
        event.payload.changed_files || state.report.changedFiles,
      );
    }
    if (Array.isArray(event.payload.changed_files)) {
      event.payload.changed_files.forEach((file) => upsertFile(file));
      if (event.payload.changed_files.length) {
        state.report.changedFiles = event.payload.changed_files.map((file) =>
          typeof file === "string" ? file : file.path,
        );
      }
    }
    state.activeTab = "diff";
  }

  if (eventType === "metrics_updated" && event.payload) {
    state.metrics.tokens = event.payload.total_tokens || state.metrics.tokens;
  }

  if (eventType === "test_finished" && event.payload?.status) {
    state.metrics.tests = event.payload.status === "passed" ? "passed" : event.payload.status;
  }

  if (eventType === "preview_started" && event.payload?.preview_url) {
    state.previewUrl = event.payload.preview_url;
    state.activeTab = "preview";
  }

  if (eventType === "report_ready" && event.payload?.markdown) {
    state.report.markdown = event.payload.markdown;
    if (Array.isArray(event.payload.changed_files)) {
      state.report.changedFiles = event.payload.changed_files.map((file) => file.path || file);
    }
    state.activeTab = "report";
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
    state.activeTab = "report";
  }

  if (eventType === "done") {
    state.status = event.payload?.status || "completed";
    state.showCompletedTasks = false;
    settleTasksForRunStatus(state.status);
    updateCurrentRunStatus(state.status);
    closeEventSource();
    if (options.hydrateOnDone !== false) {
      hydrateRunArtifacts(state.currentThreadId);
      refreshReplayEvents(state.currentThreadId);
    }
  }

  if (eventType === "error") {
    state.status = "failed";
    updateCurrentRunStatus("failed");
    closeEventSource();
  }

  if (options.renderAfter !== false) {
    render();
  }
}

export function updateCurrentRunStatus(state, status, nowTime) {
  const currentRun = state.runs.find((run) => run.id === state.currentThreadId);
  if (currentRun) {
    currentRun.status = status;
  }
  if (state.currentConversationId) {
    const conversation = state.runs.find((run) => run.id === state.currentConversationId);
    if (conversation) {
      conversation.status = status;
      conversation.threadId = state.currentThreadId;
      conversation.time = nowTime();
    }
  }
}
