export async function importCustomSkill({ requestJson, name, content = "" }) {
  return requestJson("/api/skills/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, content, description: content.slice(0, 180) }),
  });
}

export async function loadCapabilities({ fetchJson }) {
  try {
    const result = await fetchJson("/api/capabilities");
    return Array.isArray(result.groups) ? result : null;
  } catch {
    return null;
  }
}

export async function loadMcpConfigBundle({ fetchJson }) {
  try {
    const [config, status, presets] = await Promise.all([
      fetchJson("/api/mcp/servers"),
      fetchJson("/api/capabilities/mcp/status").catch(() => ({ servers: {} })),
      fetchJson("/api/mcp/presets").catch(() => ({ presets: [] })),
    ]);
    return { config, status, presets };
  } catch {
    return null;
  }
}

export async function installMcpPreset({ requestJson, presetId }) {
  return requestJson(`/api/mcp/presets/${encodeURIComponent(presetId)}/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export async function validateMcpConfig({ requestJson, serverId }) {
  const result = await requestJson("/api/capabilities/mcp/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server_id: serverId || null }),
  });
  return result.checks || [];
}

export async function loadMcpTools({ fetchJson, serverId, refresh = true }) {
  const tools = await fetchJson(`/api/mcp/servers/${encodeURIComponent(serverId)}/tools${refresh ? "?refresh=true" : ""}`);
  const status = await fetchJson(`/api/capabilities/mcp/${encodeURIComponent(serverId)}/status`).catch(() => null);
  return { tools, status };
}

export async function saveMcpServerConfig({ requestJson, serverId, command, args, envKeys, enabled = true }) {
  return requestJson("/api/mcp/servers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server_id: serverId, command, args, env_keys: envKeys, enabled }),
  });
}

export async function setMcpServerEnabled({ requestJson, serverId, enabled }) {
  return requestJson(`/api/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function deleteMcpServer({ requestJson, serverId }) {
  return requestJson(`/api/mcp/servers/${encodeURIComponent(serverId)}`, { method: "DELETE" });
}

export async function probeMcpServer({ requestJson, serverId }) {
  return requestJson(`/api/mcp/servers/${encodeURIComponent(serverId)}/probe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export async function loadSkillDetail({ fetchJson, skillId }) {
  return fetchJson(`/api/skills/${encodeURIComponent(skillId)}`);
}

export async function saveSkillContent({ requestJson, skillId, content }) {
  return requestJson(`/api/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function deleteSkill({ requestJson, skillId }) {
  return requestJson(`/api/skills/${encodeURIComponent(skillId)}`, { method: "DELETE" });
}

export async function loadSkills({ fetchJson }) {
  return fetchJson("/api/skills");
}

export async function setSkillEnabled({ requestJson, skillId, enabled }) {
  return requestJson(`/api/skills/${encodeURIComponent(skillId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function previewGitHubSkillImport({ requestJson, repoUrl, ref = "", path = "" }) {
  return requestJson("/api/skills/import/github/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_url: repoUrl, ref, path }),
  });
}

export async function importGitHubSkill({ requestJson, repoUrl, ref = "", path = "", candidateId = "", enabled = null }) {
  return requestJson("/api/skills/import/github", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_url: repoUrl,
      ref,
      path,
      candidate_id: candidateId,
      enabled,
    }),
  });
}

export async function recommendCapabilities({ requestJson, prompt }) {
  return requestJson("/api/capabilities/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}
