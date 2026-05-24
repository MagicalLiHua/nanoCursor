export async function loadConversation({ fetchJson, conversationId, query = "" }) {
  const result = await fetchJson(`/api/conversations/${encodeURIComponent(conversationId)}${query}`);
  return result.conversation || result || null;
}

export async function createTeamAgent({ requestJson, name, role, goal = "", tools = [], capabilities = [] }) {
  return requestJson("/api/team/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, role, goal, tools, capabilities }),
  });
}

export async function saveConversationTeam({ requestJson, conversationId, members, workspaceDir }) {
  return requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/team`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ members, workspace_dir: workspaceDir || undefined }),
  });
}

export async function recommendConversationTeam({ requestJson, conversationId, prompt, workspaceDir }) {
  return requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/team/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, workspace_dir: workspaceDir || undefined }),
  });
}

export async function loadEphemeralAgents({ fetchJson, threadId, includeArchived = false }) {
  const query = includeArchived ? "?include_archived=true" : "";
  return fetchJson(`/api/runs/${encodeURIComponent(threadId)}/agents${query}`);
}

export async function suggestEphemeralAgents({ requestJson, threadId, prompt, maxAgents = 4, mcpPlan = [] }) {
  return requestJson(`/api/runs/${encodeURIComponent(threadId)}/agents/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: prompt || "",
      max_agents: maxAgents,
      mcp_plan: mcpPlan,
    }),
  });
}

export async function spawnEphemeralAgent({ requestJson, threadId, agent }) {
  return requestJson(`/api/runs/${encodeURIComponent(threadId)}/agents/spawn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent }),
  });
}

export async function completeEphemeralAgent({ requestJson, threadId, agentId, summary }) {
  return requestJson(`/api/runs/${encodeURIComponent(threadId)}/agents/${encodeURIComponent(agentId)}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      summary,
      evidence: [],
      risks: [],
      artifacts: [],
      recommended_next_actions: ["交给 Lead 汇总到交付报告。"],
    }),
  });
}

export async function archiveEphemeralAgent({ requestJson, threadId, agentId, reason }) {
  return requestJson(`/api/runs/${encodeURIComponent(threadId)}/agents/${encodeURIComponent(agentId)}/archive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}
