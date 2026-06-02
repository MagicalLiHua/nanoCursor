export function mapRunHistoryItem(run, { runTitle, formatTime }) {
  return {
    id: run.thread_id,
    kind: "run",
    title: runTitle(run.prompt, run.thread_id),
    status: run.status || "unknown",
    time: formatTime(run.updated_at || run.created_at),
    mode: run.mode || "agenthub_delivery",
    workspaceDir: run.workspace_dir || "",
    prompt: run.prompt || "",
    eventCount: run.event_count || 0,
    changedFilesCount: run.changed_files_count || 0,
    hasDiff: Boolean(run.has_diff),
    hasReport: Boolean(run.has_report),
    lastEventType: run.last_event_type || "",
  };
}

export function mapConversationItem(conversation, { runTitle, formatTime }) {
  const conversationId = conversation.conversation_id || conversation.id;
  return {
    id: conversationId,
    kind: "conversation",
    conversationId,
    threadId: conversation.current_thread_id || "",
    title: conversation.title || runTitle(conversation.prompt, "新会话"),
    status: conversation.status || "draft",
    time: formatTime(conversation.updated_at || conversation.created_at),
    prompt: conversation.prompt || "",
    workspaceDir: conversation.workspace_dir || "",
    runIds: Array.isArray(conversation.run_ids) ? conversation.run_ids : [],
    agentCount: conversation.team_summary?.agent_count || conversation.team?.members?.length || 0,
  };
}

export function fileType(path, isDir = false) {
  if (isDir) return "dir";
  const ext = path.split(".").pop();
  return ext && ext !== path ? ext : "txt";
}

export function mapBackendTasks(tasks, deps) {
  return tasks.map((task) => normalizeTask(task, deps)).filter(Boolean);
}

export function tasksFromExecutionPlan(executionPlan, deps) {
  const tasks = Array.isArray(executionPlan?.tasks) ? executionPlan.tasks : [];
  const stages = Array.isArray(executionPlan?.stages) ? executionPlan.stages : [];
  const stageById = new Map(stages.map((stage) => [stage.id, stage]));
  return tasks
    .map((task) => {
      const stageId = stageIdFromTaskId(task.id);
      const stage = stageById.get(stageId) || {};
      return normalizeTask(
        {
          ...task,
          title: task.title || stage.title,
          description: task.description || stage.description,
          status: stage.status || task.status,
          owner: task.owner || stage.owner,
          capabilities: task.capabilities?.length ? task.capabilities : stage.capabilities,
          tool_evidence: task.tool_evidence || stage.tool_evidence,
          failure: task.failure || stage.failure,
          source: "execution_plan",
        },
        deps,
      );
    })
    .filter(Boolean);
}

export function stageIdFromTaskId(taskId = "") {
  const match = String(taskId).match(/^stage-\d+-(.+)$/);
  return match ? match[1] : "";
}

export function mapBackendTeam(members, { agentToneFromName }) {
  const initialsByRole = {
    lead: "L",
    planner: "P",
    coder: "C",
    tester: "T",
    reviewer: "R",
    designer: "D",
    devops: "O",
  };
  return members.map((member) => {
    const role = String(member.role || "agent").toLowerCase();
    const tone = agentToneFromName(`${member.name || ""} ${role}`, "lead");
    return {
      name: member.name || role,
      role: member.role || "agent",
      status: member.status || "idle",
      initials: initialsByRole[role] || String(member.name || "A").slice(0, 1).toUpperCase(),
      tone,
      goal: member.goal || "",
      tools: Array.isArray(member.tools) ? member.tools : [],
      capabilities: Array.isArray(member.capabilities) ? member.capabilities : [],
      lastAction: member.last_action || member.lastAction || "",
      artifacts: Array.isArray(member.artifacts) ? member.artifacts : [],
      source: member.source || "workspace",
    };
  });
}

export function normalizeTask(task, { inferTaskCapabilities }) {
  if (!task?.id) return null;
  const title = String(task.title || task.subject || "").trim();
  const description = String(task.description || task.goal || "").trim();
  if (!title && !description) return null;
  const rawStatus = String(task.status || "pending").toLowerCase();
  const statusMap = {
    passed: "completed",
    ready: "pending",
    blocked: "blocked",
    running: "running",
    failed: "failed",
    skipped: "completed",
    cancelled: "cancelled",
  };
  const normalized = {
    id: task.id,
    title,
    description,
    status: statusMap[rawStatus] || rawStatus || "pending",
    owner: task.owner || task.agent_role || "Agent",
    kind: task.kind || task.type || "",
    blockedBy: Array.isArray(task.blocked_by) ? task.blocked_by : Array.isArray(task.dependencies) ? task.dependencies : [],
    writesFiles: Boolean(task.writes_files),
    canParallel: Boolean(task.can_parallel),
    source: task.source || "task_board",
    capabilities: Array.isArray(task.capabilities) ? task.capabilities : [],
    toolEvidence: Array.isArray(task.toolEvidence)
      ? task.toolEvidence
      : Array.isArray(task.tool_evidence)
        ? task.tool_evidence
        : [],
    evidencePreview: Array.isArray(task.evidence_preview) ? task.evidence_preview : [],
    outputPreview: Array.isArray(task.output_preview) ? task.output_preview : [],
    evidenceCount: Number(task.evidence_count || 0),
    outputCount: Number(task.output_count || 0),
    failure: task.failure || "",
  };
  normalized.capabilities = normalized.capabilities.length ? normalized.capabilities : inferTaskCapabilities(normalized);
  return normalized;
}
