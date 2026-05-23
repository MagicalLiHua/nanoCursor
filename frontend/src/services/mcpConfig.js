export function normalizeMcpConfig(raw = {}, { status = null, presetsPayload = null, previous = {} } = {}) {
  const config = raw.mcp && typeof raw.mcp === "object" ? raw.mcp : raw;
  const statusByServer = status?.servers || config.statusByServer || previous.statusByServer || {};
  const toolsByServer = config.toolsByServer || previous.toolsByServer || {};
  const presets = Array.isArray(presetsPayload?.presets)
    ? presetsPayload.presets
    : Array.isArray(config.presets)
      ? config.presets
      : previous.presets || [];

  return {
    servers: Array.isArray(config.servers) ? config.servers : [],
    presets,
    config_paths: Array.isArray(config.config_paths) ? config.config_paths : [],
    summary: config.summary || {},
    presetSummary: presetsPayload?.summary || config.presetSummary || previous.presetSummary || {},
    validation: config.validation || previous.validation || null,
    validationByServer: config.validationByServer || previous.validationByServer || {},
    statusByServer,
    toolsByServer,
  };
}

export function parseMcpArgs(raw) {
  const text = (raw || "").trim();
  if (!text) return [];
  const lines = text.split(/\n+/).map((item) => item.trim()).filter(Boolean);
  if (lines.length > 1) return lines;
  return text.split(/\s+/).map((item) => item.trim()).filter(Boolean);
}

export function parseMcpEnvKeys(raw) {
  return (raw || "")
    .split(/[,\n\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}
