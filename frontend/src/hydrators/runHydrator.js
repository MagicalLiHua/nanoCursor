import {
  blankEphemeralAgents,
  normalizeEphemeralAgentsResult,
} from "../state/runDefaults.js";

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

  if (diffResult.status === "fulfilled") {
    const diffInfo = diffResult.value;
    const diffText =
      diffInfo.diff ||
      `No diff detected for ${threadId}.\n\nChanged files: ${(diffInfo.changed_files || [])
        .map((file) => file.path)
        .join(", ") || "none"}`;
    setDiffState(diffText, diffInfo.changed_files || []);
    if (Array.isArray(diffInfo.changed_files) && diffInfo.changed_files.length) {
      state.report.changedFiles = diffInfo.changed_files.map((file) => file.path);
      state.metrics.files = diffInfo.changed_files.length;
    }
  }

  if (reportResult.status === "fulfilled") {
    const report = reportResult.value;
    state.report.summary = report.summary || state.report.summary;
    state.report.markdown = report.markdown || "";
    state.report.changedFiles = (report.changed_files || state.report.changedFiles).map((item) =>
      typeof item === "string" ? item : item.path,
    );
    state.report.risks = report.risks?.length ? report.risks : state.report.risks;
  }

  if (traceabilityResult.status === "fulfilled") {
    setTraceability(traceabilityResult.value);
  }

  if (artifactsResult.status === "fulfilled") {
    state.artifactCenter = artifactsResult.value;
  }

  if (recoveryResult.status === "fulfilled") {
    state.recoveryCenter = recoveryResult.value;
  }

  if (deliveryResult.status === "fulfilled") {
    const delivery = deliveryResult.value;
    if (delivery) {
      state.report.delivery = delivery;
      state.report.summary = delivery.summary || state.report.summary;
      state.report.changedFiles = (delivery.changed_files || []).map((file) => file.path || file);
      state.report.risks = delivery.risks || state.report.risks;
      state.currentRunStatus = delivery.status;
    }
  }

  if (changesResult.status === "fulfilled") {
    const changeSet = changesResult.value;
    if (changeSet?.files) {
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
