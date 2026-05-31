export function blankReport() {
  return {
    summary: "",
    markdown: "",
    source: "",
    requirements: [],
    changedFiles: [],
    risks: [],
    traceability: {
      source: "history",
      coverageRate: 0,
      totalCount: 0,
      coveredCount: 0,
      partialCount: 0,
      missingCount: 0,
      requirements: [],
    },
  };
}

export function blankArtifactCenter(status = "") {
  return {
    status,
    summary: {},
    artifacts: [],
  };
}

export function blankRecoveryCenter(status = "") {
  return {
    status,
    summary: {},
    recovery_points: [],
    risks: [],
    actions: [],
  };
}

export function blankApproval(demoState) {
  return structuredClone(demoState.approval);
}

export function blankEphemeralAgents() {
  return {
    status: "idle",
    suggestions: [],
    agents: [],
    active_count: 0,
    archived_count: 0,
    total: 0,
    includeArchived: false,
    limits: {
      max_active_agents: 3,
      max_suggested_agents: 5,
    },
    mcp_plan_count: 0,
    error: "",
  };
}

export function normalizeEphemeralAgentsResult(result = {}, previous = blankEphemeralAgents()) {
  const agents = Array.isArray(result.agents) ? result.agents : previous.agents || [];
  const suggestions = Array.isArray(result.suggestions) ? result.suggestions : previous.suggestions || [];
  const activeCount =
    typeof result.active_count === "number"
      ? result.active_count
      : agents.filter((agent) => !["archived", "expired"].includes(agent.status)).length;
  return {
    ...blankEphemeralAgents(),
    ...previous,
    ...result,
    status: result.status || "ready",
    suggestions,
    agents,
    active_count: activeCount,
    archived_count: typeof result.archived_count === "number" ? result.archived_count : previous.archived_count || 0,
    total: typeof result.total === "number" ? result.total : agents.length,
    limits: {
      ...(previous.limits || {}),
      ...(result.limits || {}),
    },
    includeArchived:
      typeof result.includeArchived === "boolean"
        ? result.includeArchived
        : Boolean(previous.includeArchived),
    error: "",
  };
}
