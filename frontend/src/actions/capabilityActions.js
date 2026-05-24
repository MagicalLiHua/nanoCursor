export async function importCustomSkill({ requestJson, name, content = "" }) {
  return requestJson("/api/capabilities/skills", {
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
      fetchJson("/api/capabilities/mcp"),
      fetchJson("/api/capabilities/mcp/status").catch(() => ({ servers: {} })),
      fetchJson("/api/capabilities/mcp/presets").catch(() => ({ presets: [] })),
    ]);
    return { config, status, presets };
  } catch {
    return null;
  }
}

export async function installMcpPreset({ requestJson, presetId }) {
  return requestJson(`/api/capabilities/mcp/presets/${encodeURIComponent(presetId)}/install`, {
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
  const tools = await fetchJson(`/api/capabilities/mcp/${encodeURIComponent(serverId)}/tools${refresh ? "?refresh=true" : ""}`);
  const status = await fetchJson(`/api/capabilities/mcp/${encodeURIComponent(serverId)}/status`).catch(() => null);
  return { tools, status };
}

export async function saveMcpServerConfig({ requestJson, serverId, command, args, envKeys }) {
  return requestJson("/api/capabilities/mcp/servers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server_id: serverId, command, args, env_keys: envKeys }),
  });
}

export async function loadSkillDetail({ fetchJson, skillId }) {
  return fetchJson(`/api/capabilities/skills/${encodeURIComponent(skillId)}`);
}

export async function saveSkillContent({ requestJson, skillId, content }) {
  return requestJson(`/api/capabilities/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function deleteSkill({ requestJson, skillId }) {
  return requestJson(`/api/capabilities/skills/${encodeURIComponent(skillId)}`, { method: "DELETE" });
}

export async function recommendCapabilities({ requestJson, prompt }) {
  return requestJson("/api/capabilities/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}
