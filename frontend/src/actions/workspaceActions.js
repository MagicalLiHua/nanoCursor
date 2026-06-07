export async function loadRecentProjects({ fetchJson }) {
  try {
    const result = await fetchJson("/api/workspace/recent");
    return result.recent || [];
  } catch {
    return [];
  }
}

export async function loadWorkspaceState({ fetchJson }) {
  try {
    const result = await fetchJson("/api/workspaces");
    return {
      current: result.current_workspace || "",
      meta: {
        default_workspace: result.default_workspace || "",
        workspace_root: result.workspace_root || "",
        project_root: result.project_root || "",
        is_default_workspace: Boolean(result.is_default_workspace),
      },
    };
  } catch {
    return null;
  }
}

export async function loadFiletoolsStatus({ fetchJson }) {
  try {
    return await fetchJson("/api/runtime/filetools/status");
  } catch (error) {
    return {
      enabled: false,
      fallback_enabled: true,
      healthy: false,
      backend: "python",
      error: error?.message || "filetools status unavailable",
    };
  }
}

export async function loadIndexerStatus({ fetchJson }) {
  try {
    return await fetchJson("/api/runtime/indexer/status");
  } catch (error) {
    return {
      enabled: false,
      fallback_enabled: true,
      healthy: false,
      backend: "python",
      indexed_files: 0,
      error: error?.message || "indexer status unavailable",
    };
  }
}

export async function loadWorkspaceOverview({ fetchJson, workspaceDir, previousOverview = {} }) {
  try {
    const query = workspaceDir ? `?workspace_dir=${encodeURIComponent(workspaceDir)}` : "";
    const overview = await fetchJson(`/api/workspace/overview${query}`);
    return {
      ...previousOverview,
      ...overview,
      summary: { ...(previousOverview?.summary || {}), ...(overview.summary || {}) },
      project_index: { ...(previousOverview?.project_index || {}), ...(overview.project_index || {}) },
      recovery: { ...(previousOverview?.recovery || {}), ...(overview.recovery || {}) },
    };
  } catch {
    return {
      ...previousOverview,
      workspace_dir: workspaceDir || previousOverview?.workspace_dir || "",
    };
  }
}

export async function openWorkspacePath({ requestJson, path }) {
  return requestJson("/api/workspaces/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function createConversationDraft({ requestJson, workspaceDir, prompt = "" }) {
  const result = await requestJson("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, workspace_dir: workspaceDir || undefined }),
  });
  return result.conversation || null;
}

export async function loadRunHistorySnapshot({
  fetchJson,
  workspaceDir,
  existingRuns = [],
  mapRunHistoryItem,
  mapConversationItem,
}) {
  try {
    const workspaceQuery = workspaceDir ? `&workspace_dir=${encodeURIComponent(workspaceDir)}` : "";
    const [runsResult, conversationsResult] = await Promise.allSettled([
      fetchJson(`/api/runs?limit=50${workspaceQuery}`),
      fetchJson(`/api/conversations?limit=50${workspaceDir ? `&workspace_dir=${encodeURIComponent(workspaceDir)}` : ""}`),
    ]);
    const runs = runsResult.status === "fulfilled" ? (runsResult.value.runs || []).map(mapRunHistoryItem) : [];
    const conversations =
      conversationsResult.status === "fulfilled"
        ? (conversationsResult.value.conversations || []).map(mapConversationItem)
        : [];
    const runIdsLinkedToConversations = new Set(conversations.flatMap((item) => item.runIds || []));
    const standaloneRuns = runs.filter((run) => !runIdsLinkedToConversations.has(run.id));
    const transientRuns = existingRuns.filter(
      (run) =>
        (run.localOnly || run.status === "running") &&
        (!workspaceDir || !run.workspaceDir || run.workspaceDir === workspaceDir) &&
        !standaloneRuns.some((item) => item.id === run.id) &&
        !conversations.some((item) => item.id === run.id),
    );
    return {
      conversations,
      runs: [...transientRuns, ...conversations, ...standaloneRuns],
    };
  } catch {
    return null;
  }
}
