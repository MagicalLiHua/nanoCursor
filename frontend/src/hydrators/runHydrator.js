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
    state.report.risks = report.risks?.length ? report.risks : state.report.risks;
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
      state.report.risks = delivery.risks || state.report.risks;
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
    state.report.risks = Array.isArray(outcome.report.risks) && outcome.report.risks.length
      ? outcome.report.risks
      : state.report.risks;
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

function hasChangeEvidence(outcome = {}) {
  const changes = outcome.changes || {};
  return Boolean(changes.diff || (Array.isArray(changes.files) && changes.files.length));
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
