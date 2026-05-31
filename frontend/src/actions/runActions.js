export async function loadReplayEvents({ fetchJson, threadId }) {
  const result = await fetchJson(`/api/runs/${encodeURIComponent(threadId)}/events/history`);
  return result.events || [];
}

export async function loadRunSessionAndEvents({ fetchJson, threadId }) {
  const [sessionResult, eventsResult] = await Promise.allSettled([
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/events/history`),
  ]);
  return { sessionResult, eventsResult };
}

export async function loadRunSession({ fetchJson, threadId }) {
  return fetchJson(`/api/runs/${encodeURIComponent(threadId)}`);
}

export async function startRun({ requestJson, conversationId, prompt, workspaceDir, messages = [], demo = false }) {
  const endpoint = demo
    ? "/api/runs/demo"
    : `/api/conversations/${encodeURIComponent(conversationId)}/runs`;
  return requestJson(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, workspace_dir: workspaceDir || undefined, messages }),
  });
}

export async function cancelRun({ requestJson, threadId }) {
  return requestJson(`/api/runs/${encodeURIComponent(threadId)}/cancel`, {
    method: "POST",
  });
}

export async function loadWorkspaceDataSnapshot({ fetchJson, includeRunState = true }) {
  const results = await Promise.allSettled([
    fetchJson("/api/files"),
    includeRunState ? fetchJson("/api/tasks") : Promise.resolve({ tasks: [] }),
    includeRunState ? fetchJson("/api/team") : Promise.resolve({ members: [] }),
  ]);
  const [filesResult, tasksResult, teamResult] = results;
  return { filesResult, tasksResult, teamResult };
}

export async function loadRunArtifactsBundle({ fetchJson, threadId, includeArchived = false }) {
  const results = await Promise.allSettled([
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/outcome`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/diff`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/report`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/traceability`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/artifacts`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/recovery`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/delivery`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/changes`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/failures`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/agents?include_archived=${includeArchived ? "true" : "false"}`),
  ]);
  const [
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
  ] = results;
  return {
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
  };
}

export async function loadBenchmarks({ fetchJson }) {
  try {
    const result = await fetchJson("/api/benchmarks");
    return Array.isArray(result.benchmarks) ? result.benchmarks : [];
  } catch {
    return [];
  }
}

export async function startBenchmark({ requestJson, benchmarkId, workspaceDir }) {
  return requestJson("/api/benchmarks/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ benchmark_id: benchmarkId, workspace_dir: workspaceDir || undefined }),
  });
}

export async function loadMemoryProfile({ fetchJson }) {
  return fetchJson("/api/preferences/profile");
}

export async function loadRecoveryCenter({ fetchJson }) {
  return fetchJson("/api/recovery");
}

export async function createPreference({ requestJson, preferenceType, content, importance = 8 }) {
  return requestJson("/api/preferences", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      preference_type: preferenceType,
      content,
      importance,
    }),
  });
}
