import {
  blankEphemeralAgents,
  normalizeEphemeralAgentsResult,
} from "../state/runDefaults.js";
import { agentToneFromName, formatTime } from "../core/format.js";
import { mapBackendTeam, normalizeTask } from "../state/mappers.js";
import { inferTaskCapabilities } from "../state/selectors.js";
import { buildAgentActivityQueue } from "../state/chatState.js";

export function mapTraceability(traceability = {}) {
  return {
    source: traceability.source || "generated",
    coverageRate: traceability.coverage_rate || 0,
    totalCount: traceability.total_count || 0,
    coveredCount: traceability.covered_count || 0,
    partialCount: traceability.partial_count || 0,
    missingCount: traceability.missing_count || 0,
    requirements: traceability.requirements || [],
  };
}

export function applyTraceability(state, traceability) {
  state.report.traceability = mapTraceability(traceability);
  state.report.requirements = state.report.traceability.requirements.map(
    (item) => `${item.id}: ${item.title}`,
  );
}

export function applyRunArtifactsBundle({
  state,
  bundle,
  threadId,
  setDiffState,
  setTraceability = (traceability) => applyTraceability(state, traceability),
}) {
  const {
    outcomeResult,
    diffResult,
    reportResult,
    traceabilityResult,
    artifactsResult,
    recoveryResult,
    deliveryResult,
    changesResult,
    failuresResult,
    agentsResult,
  } = bundle;

  if (outcomeResult?.status === "fulfilled") {
    applyRunOutcome({
      state,
      outcome: outcomeResult.value,
      setDiffState,
      setTraceability,
    });
  }

  const hasOutcome = Boolean(state.runOutcome);
  const hasOutcomeChanges = hasOutcome && hasChangeEvidence(state.runOutcome);
  const hasOutcomeReport = hasOutcome && hasReportEvidence(state.runOutcome);
  const hasOutcomeTraceability = hasOutcome && hasTraceabilityEvidence(state.runOutcome);
  const hasOutcomeArtifacts = hasOutcome && hasArtifactEvidence(state.runOutcome);
  const hasOutcomeRecovery = hasOutcome && hasRecoveryEvidence(state.runOutcome);

  if (diffResult.status === "fulfilled") {
    const diffInfo = diffResult.value;
    const diffText =
      diffInfo.diff ||
      `No diff detected for ${threadId}.\n\nChanged files: ${(diffInfo.changed_files || [])
        .map((file) => file.path)
        .join(", ") || "none"}`;
    if (!hasOutcomeChanges) {
      setDiffState(diffText, diffInfo.changed_files || []);
    }
    if (!hasOutcomeChanges && Array.isArray(diffInfo.changed_files) && diffInfo.changed_files.length) {
      state.report.changedFiles = diffInfo.changed_files.map((file) => file.path);
      state.metrics.files = diffInfo.changed_files.length;
    }
  }

  if (reportResult.status === "fulfilled") {
    const report = reportResult.value;
    state.report.summary = report.summary || state.report.summary;
    if (!hasOutcomeReport || report.markdown) {
      state.report.markdown = report.markdown || state.report.markdown;
      state.report.source = report.source || state.report.source;
    }
    if (!hasOutcomeChanges || (Array.isArray(report.changed_files) && report.changed_files.length)) {
      state.report.changedFiles = (report.changed_files || state.report.changedFiles).map((item) =>
        typeof item === "string" ? item : item.path,
      );
    }
    if (Array.isArray(report.risks)) {
      state.report.risks = report.risks;
    }
  }

  if (traceabilityResult.status === "fulfilled" && !hasOutcomeTraceability) {
    setTraceability(traceabilityResult.value);
  }

  if (artifactsResult.status === "fulfilled" && (!hasOutcomeArtifacts || hasArtifactCenterEvidence(artifactsResult.value))) {
    state.artifactCenter = artifactsResult.value;
  }

  if (recoveryResult.status === "fulfilled" && (!hasOutcomeRecovery || hasRecoveryCenterEvidence(recoveryResult.value))) {
    state.recoveryCenter = recoveryResult.value;
  }

  if (deliveryResult.status === "fulfilled") {
    const delivery = deliveryResult.value;
    if (delivery) {
      state.report.delivery = delivery;
      state.report.summary = delivery.summary || state.report.summary;
      if (!hasOutcomeChanges && Array.isArray(delivery.changed_files) && delivery.changed_files.length) {
        state.report.changedFiles = delivery.changed_files.map((file) => file.path || file);
      }
      if (Array.isArray(delivery.risks)) {
        state.report.risks = delivery.risks;
      }
      state.currentRunStatus = delivery.status;
    }
  }

  if (changesResult.status === "fulfilled") {
    const changeSet = changesResult.value;
    if (changeSet?.files && (!hasOutcomeChanges || !state.diffFiles?.length)) {
      state.diffFiles = changeSet.files.map((file) => ({
        path: file.path,
        changeType: file.change_type,
        risk: file.risk,
        additions: file.additions,
        deletions: file.deletions,
      }));
      state.metrics.files = changeSet.files.length;
    }
  }

  if (failuresResult.status === "fulfilled") {
    const failures = failuresResult.value;
    if (failures?.failures) {
      state.recoveryCenter = state.recoveryCenter || {};
      state.recoveryCenter.failures = failures.failures;
    }
  }

  if (agentsResult.status === "fulfilled") {
    state.ephemeralAgents = normalizeEphemeralAgentsResult(
      {
        ...agentsResult.value,
        includeArchived: Boolean(state.ephemeralAgents?.includeArchived),
        suggestions: state.ephemeralAgents?.suggestions || [],
      },
      state.ephemeralAgents || blankEphemeralAgents(),
    );
  }
}

export function applyRunOutcome({ state, outcome, setDiffState, setTraceability }) {
  if (!outcome || typeof outcome !== "object") return;
  state.runOutcome = outcome;

  const changes = outcome.changes || {};
  const changedFiles = Array.isArray(changes.files) ? changes.files : [];
  if (changes.diff || changedFiles.length) {
    setDiffState(changes.diff || "", changedFiles);
    state.report.changedFiles = changedFiles.map((file) => file.path || file);
    state.metrics.files = changedFiles.length;
  }

  if (outcome.report) {
    state.report.summary = outcome.report.summary || outcome.summary?.final_message || state.report.summary;
    state.report.markdown = outcome.report.markdown || "";
    state.report.source = outcome.report.source || "";
    if (Array.isArray(outcome.report.risks)) {
      state.report.risks = outcome.report.risks;
    }
  }

  if (outcome.traceability) {
    setTraceability(outcome.traceability);
  }

  if (outcome.artifacts) {
    state.artifactCenter = outcome.artifacts;
  }

  if (outcome.recovery) {
    state.recoveryCenter = outcome.recovery;
  }

  if (outcome.quality) {
    state.report.quality = outcome.quality;
  }

  if (outcome.status) {
    state.currentRunStatus = outcome.status;
  }
}

export function applyRunSnapshot({
  state,
  snapshot,
  setDiffState,
  setTraceability = (traceability) => applyTraceability(state, traceability),
  replaceMessages = false,
}) {
  if (!snapshot || typeof snapshot !== "object") return;
  const snapshotThreadId = snapshot.run?.thread_id;
  if (
    snapshotThreadId &&
    state.currentThreadId &&
    state.currentThreadId !== "pending" &&
    snapshotThreadId !== state.currentThreadId
  ) {
    return;
  }

  state.runSnapshot = snapshot;

  const run = snapshot.run || {};
  const workspace = snapshot.workspace || {};
  const conversation = snapshot.conversation || {};
  const changes = snapshot.changes || {};
  const quality = snapshot.quality || {};
  const timeline = Array.isArray(snapshot.timeline) ? snapshot.timeline : [];

  // A snapshot is authoritative for the selected run. Clear run-scoped
  // evidence first so an empty field cannot leave stale data from another run.
  state.runOutcome = null;
  state.diff = "";
  state.diffFiles = [];
  state.selectedDiffFile = "";
  setDiffState?.("", []);
  state.report.quality = null;
  state.report.risks = [];
  state.report.changedFiles = [];
  state.recoveryCenter = {
    ...(state.recoveryCenter || {}),
    risks: [],
  };

  if (run.thread_id) state.currentThreadId = run.thread_id;
  state.currentRunStrategy = run.strategy || state.currentRunStrategy || "";
  if (run.status) {
    state.status = run.status;
    state.currentRunStatus = run.status;
  }
  if (workspace.path) {
    state.workspaceDir = workspace.path;
    state.workspaceInput = workspace.path;
  }
  if (conversation.conversation_id) {
    state.currentConversationId = conversation.conversation_id;
  }

  const mappedMessages = mapSnapshotMessages(conversation.messages || []);
  if (mappedMessages.length) {
    if (replaceMessages || !Array.isArray(state.messages) || state.messages.length === 0) {
      state.messages = mappedMessages;
    } else {
      state.messages = mergeConversationMessages(state.messages, mappedMessages);
    }
  }

  state.events = timeline.map(mapSnapshotTimelineEvent);
  state.replay = {
    ...(state.replay || {}),
    events: timeline,
    index: timeline.length,
    status: timeline.length ? "ready" : "idle",
    prompt: run.prompt || state.replay?.prompt || "",
    startedAt: formatTime(run.created_at) || state.replay?.startedAt || "",
  };

  const tasks = Array.isArray(snapshot.tasks) ? snapshot.tasks : [];
  state.tasks = run.strategy === "lead_direct_reply"
    ? []
    : tasks.map((task) => normalizeTask(task, { inferTaskCapabilities })).filter(Boolean);
  const filesChanged = Number(changes.files_changed ?? (Array.isArray(changes.files) ? changes.files.length : 0));
  state.metrics = {
    ...(state.metrics || {}),
    tasks: state.tasks.length,
    files: filesChanged,
    insertions: Number(changes.insertions ?? 0),
    deletions: Number(changes.deletions ?? 0),
  };

  const outcome = snapshot.outcome && typeof snapshot.outcome === "object" ? snapshot.outcome : null;
  if (outcome && Object.keys(outcome).length) {
    applyRunOutcome({ state, outcome, setDiffState, setTraceability });
  } else if (Array.isArray(changes.files) && changes.files.length) {
    setDiffState?.("", changes.files);
    state.report.changedFiles = changes.files.map((file) => file.path || file).filter(Boolean);
  }

  if (Array.isArray(snapshot.artifacts) && snapshot.artifacts.length) {
    state.artifactCenter = {
      ...(state.artifactCenter || {}),
      status: snapshot.run?.status || state.artifactCenter?.status || "ready",
      artifacts: snapshot.artifacts,
    };
  }

  if (quality && Object.keys(quality).length) {
    state.report.quality = quality;
    if (Array.isArray(quality.risks)) {
      state.report.risks = quality.risks;
      state.recoveryCenter = {
        ...(state.recoveryCenter || {}),
        risks: quality.risks,
      };
    }
  }

  state.agentActivities = run.is_active
    ? mapSnapshotActivities(snapshot.activity?.items || [], state.agentActivities || [])
    : [];
  state.ephemeralAgents = normalizeEphemeralAgentsResult(
    {
      agents: Array.isArray(snapshot.agents) ? snapshot.agents : [],
      active_count: Array.isArray(snapshot.agents)
        ? snapshot.agents.filter((agent) => !["archived", "expired"].includes(agent.status)).length
        : 0,
      total: Array.isArray(snapshot.agents) ? snapshot.agents.length : 0,
      includeArchived: Boolean(state.ephemeralAgents?.includeArchived),
    },
    state.ephemeralAgents || blankEphemeralAgents(),
  );

  const teamMembers = mapSnapshotTeamMembers(snapshot);
  if (teamMembers.length) {
    state.team = teamMembers;
  }

  const pendingApproval = Array.isArray(snapshot.approvals) ? snapshot.approvals[0] : null;
  if (pendingApproval) {
    state.approval = mapSnapshotApproval(pendingApproval);
  } else if (state.approval?.kind === "tool" && state.approval?.status === "pending") {
    state.approval = { status: "idle", planId: "", title: "", content: "", riskLevel: "", tasks: [], decision: "", comment: "" };
  }
}

function hasChangeEvidence(outcome = {}) {
  const changes = outcome.changes || {};
  return Boolean(changes.diff || (Array.isArray(changes.files) && changes.files.length));
}

function mapSnapshotMessages(messages = []) {
  return messages
    .filter((message) => String(message?.content || "").trim())
    .map((message) => ({
      role: message.role === "assistant" ? "assistant" : "user",
      author: message.role === "assistant"
        ? `${message.agent || "Lead"} Agent`
        : "用户",
      time: formatTime(message.timestamp) || "",
      sourceTimestamp: message.timestamp || null,
      content: message.content,
    }));
}

function sameMessage(a = {}, b = {}) {
  return a.role === b.role &&
    String(a.content || "").trim() === String(b.content || "").trim() &&
    String(a.author || "") === String(b.author || "");
}

export function mergeConversationMessages(existing = [], incoming = []) {
  const result = [...existing];
  for (const message of incoming) {
    const duplicate = result.some((item) => {
      if (!sameMessage(item, message)) return false;
      if (message.sourceTimestamp && item.sourceTimestamp) {
        return message.sourceTimestamp === item.sourceTimestamp;
      }
      return true;
    });
    if (!duplicate) result.push(message);
  }
  return result;
}

function mapSnapshotTimelineEvent(event = {}) {
  return {
    id: event.id,
    type: event.type || "event",
    title: event.title || event.type || "事件",
    content: event.content || "",
    time: formatTime(event.timestamp) || "",
    agent: event.agent || "",
    payload: event.payload || {},
  };
}

function mapSnapshotActivities(items = [], currentQueue = []) {
  const activities = items
    .filter((item) => String(item?.action || item?.title || "").trim())
    .map((item) => ({
      agent: normalizeAgentLabel(item.agent),
      title: item.title || item.type || "Agent 活动",
      text: item.action || item.title || "",
      eventType: item.type || "agent_activity",
      time: formatTime(item.timestamp) || "",
      explicitAgentWork: true,
      payload: item.payload || {},
      status: item.status || "",
      inputTokens: 0,
      outputTokens: 0,
    }));
  return buildAgentActivityQueue(activities, { initialQueue: currentQueue });
}

function mapSnapshotTeamMembers(snapshot = {}) {
  const agents = Array.isArray(snapshot.agents) ? snapshot.agents : [];
  const byName = new Map();

  byName.set("lead", {
    name: "Lead",
    role: "lead",
    status: snapshot.run?.is_active ? "running" : "idle",
    initials: "L",
    tone: "lead",
    goal: "判断任务复杂度、组织上下文，并按需创建临时 Agent。",
    lastAction: snapshot.activity?.current_action || "",
    tools: ["plan", "inspect", "delegate", "report"],
    capabilities: ["tool.project_index", "tool.memory"],
    source: "snapshot",
  });

  for (const agent of agents.filter((item) => !["archived", "expired"].includes(item.status))) {
    const name = agent.name || agent.agent_id || agent.role || "Agent";
    const role = String(agent.role || "agent").toLowerCase();
    byName.set(String(name).toLowerCase(), {
      name,
      role,
      status: agent.status || "idle",
      initials: String(name).slice(0, 1).toUpperCase(),
      tone: agentToneFromName(`${name} ${role}`, "lead"),
      goal: agent.goal || agent.reason || "",
      tools: agent.tools || [],
      capabilities: agent.capabilities || [],
      lastAction: agent.result?.summary || "",
      artifacts: agent.artifacts || [],
      source: "snapshot",
    });
  }

  return mapBackendTeam(Array.from(byName.values()), { agentToneFromName });
}

function normalizeAgentLabel(agent) {
  const raw = String(agent || "Lead").trim();
  if (!raw) return "Lead";
  const clean = raw.replace(/\s*Agent$/i, "");
  const known = {
    lead: "Lead",
    planner: "Planner",
    coder: "Coder",
    tester: "Tester",
    reviewer: "Reviewer",
    designer: "Designer",
    security: "Security",
    system: "System",
  };
  return known[clean.toLowerCase()] || clean;
}

function mapSnapshotApproval(approval = {}) {
  const decisionId = approval.id || approval.decision_id || "";
  const threadId = approval.thread_id || approval.run_id || "";
  const action = approval.action || approval.tool_call || {};
  const tool = approval.tool || action.kind || action.tool || "tool";
  return {
    kind: "tool",
    status: "pending",
    decisionId,
    threadId,
    tool,
    title: approval.title || `${tool} 需要审批`,
    content: approval.reason || approval.content || action.target || "",
    riskLevel: approval.risk_level || approval.riskLevel || "",
    tasks: [{
      id: decisionId || tool,
      title: approval.reason || `${tool}: ${action.target || "等待审批"}`,
      status: "pending",
    }],
    decision: "",
    comment: "",
  };
}

function hasReportEvidence(outcome = {}) {
  const report = outcome.report || {};
  return Boolean(report.markdown || report.summary || outcome.summary?.final_message);
}

function hasTraceabilityEvidence(outcome = {}) {
  const traceability = outcome.traceability || {};
  return Boolean(Array.isArray(traceability.requirements) && traceability.requirements.length);
}

function hasArtifactEvidence(outcome = {}) {
  return hasArtifactCenterEvidence(outcome.artifacts);
}

function hasArtifactCenterEvidence(artifactCenter = {}) {
  return Boolean(
    Array.isArray(artifactCenter.artifacts) && artifactCenter.artifacts.length
      || Object.keys(artifactCenter.summary || {}).length,
  );
}

function hasRecoveryEvidence(outcome = {}) {
  return hasRecoveryCenterEvidence(outcome.recovery);
}

function hasRecoveryCenterEvidence(recoveryCenter = {}) {
  return Boolean(
    Array.isArray(recoveryCenter.recovery_points) && recoveryCenter.recovery_points.length
      || Array.isArray(recoveryCenter.risks) && recoveryCenter.risks.length
      || Array.isArray(recoveryCenter.actions) && recoveryCenter.actions.length
      || Object.keys(recoveryCenter.summary || {}).length,
  );
}

export async function hydrateRunArtifacts({
  state,
  threadId,
  fetchJson,
  loadRunArtifactsBundle,
  refreshWorkspaceData,
  setDiffState,
  setTraceability,
  render,
  refreshWorkspace = true,
}) {
  if (refreshWorkspace) {
    await refreshWorkspaceData({ allowEmpty: true, includeRunState: false });
  }

  const bundle = await loadRunArtifactsBundle({
    fetchJson,
    threadId,
    includeArchived: Boolean(state.ephemeralAgents?.includeArchived),
  });

  applyRunArtifactsBundle({
    state,
    bundle,
    threadId,
    setDiffState,
    setTraceability,
  });

  render();
}
