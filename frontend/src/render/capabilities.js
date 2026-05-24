import {
  capabilityKindLabel,
  capabilityStatusLabel,
  escapeHtml,
  formatTime,
} from "../core/format.js";

let viewState = null;
let busy = () => false;

export function renderCapabilities({ state, isActionBusy }) {
  viewState = state;
  busy = isActionBusy;

  if (viewState.skillDetail) return renderSkillDetailPanel();

  const hub = viewState.capabilityHub || {};
  const summary = hub.summary || {};
  const groups = hub.groups || [];
  return `
    <div class="capability-panel">
      <section class="capability-summary">
        <div><strong>${escapeHtml(summary.total ?? 0)}</strong><span>全部能力</span></div>
        <div><strong>${escapeHtml(summary.ready ?? 0)}</strong><span>可用</span></div>
        <div><strong>${escapeHtml(summary.configured ?? 0)}</strong><span>已配置</span></div>
        <div><strong>${escapeHtml(summary.planned ?? 0)}</strong><span>待接入</span></div>
      </section>
      ${renderCapabilitySetupPanel()}
      ${renderMcpPresetPanel()}
      <details class="capability-config-drawer">
        <summary>
          <span>手动配置 MCP</span>
          <strong>连接自定义 Server</strong>
        </summary>
        <form class="mcp-config-panel" id="mcp-config-form">
          <div>
            <strong>配置 MCP Server</strong>
            <span>写入当前项目的 .nanocursor/mcp.json；密钥建议使用环境变量名，不直接保存明文。</span>
          </div>
          <div class="mcp-config-grid">
            <input id="mcp-server-name-input" placeholder="server 名称，例如 github" />
            <input id="mcp-command-input" placeholder="启动命令，例如 npx" />
          </div>
          <textarea id="mcp-args-input" rows="2" placeholder="参数：每行一个，例如&#10;-y&#10;@modelcontextprotocol/server-github"></textarea>
          <input id="mcp-env-input" placeholder="环境变量名，逗号分隔，例如 GITHUB_TOKEN" />
          <button class="button secondary compact-button" type="submit">保存 MCP 配置</button>
        </form>
      </details>
      <div class="capability-groups">
        ${groups.map(renderCapabilityGroup).join("")}
      </div>
    </div>
  `;
}

function renderCapabilitySetupPanel() {
  return `
    <details class="capability-config-drawer">
      <summary>
        <span>自定义 Skill</span>
        <strong>导入项目能力</strong>
      </summary>
      <form class="skill-import-panel" id="skill-import-form">
        <div>
          <strong>导入自定义 Skill</strong>
          <span>写入当前项目的 .nanocursor/skills，刷新后自动进入能力中心。</span>
        </div>
        <input id="skill-name-input" placeholder="Skill 名称，例如 api-review" />
        <textarea id="skill-content-input" rows="3" placeholder="粘贴 SKILL.md 内容，或写一段用途说明"></textarea>
        <button class="button secondary compact-button" type="submit">导入</button>
      </form>
    </details>
  `;
}

function renderMcpPresetPanel() {
  const presets = viewState.mcpConfig?.presets || [];
  if (!presets.length) return "";
  return `
    <details class="capability-config-drawer mcp-preset-drawer">
      <summary>
        <span>MCP 预设</span>
        <strong>${escapeHtml(viewState.mcpConfig?.presetSummary?.configured || 0)} 已启用</strong>
      </summary>
      <section class="mcp-preset-panel">
        <div class="mcp-preset-head">
          <div>
            <strong>推荐 MCP 预设</strong>
            <span>一键写入当前项目配置，后续仍可在下方手动微调。</span>
          </div>
        </div>
        <div class="mcp-preset-list">
          ${presets.map(renderMcpPresetCard).join("")}
        </div>
      </section>
    </details>
  `;
}

function renderMcpPresetCard(preset) {
  const installed = Boolean(preset.installed);
  const requires = Array.isArray(preset.requires) ? preset.requires : [];
  const busyKey = `install-mcp-preset:${preset.id}`;
  return `
    <article class="mcp-preset-card ${installed ? "installed" : ""}">
      <div>
        <strong>${escapeHtml(preset.name || preset.id)}</strong>
        <span>${escapeHtml(preset.description || "")}</span>
      </div>
      ${requires.length ? `<div class="mcp-preset-tags">${requires.slice(0, 3).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
      ${preset.security_note ? `<p>${escapeHtml(preset.security_note)}</p>` : ""}
      <button class="button ${installed ? "secondary" : "primary"} compact-button" data-action="install-mcp-preset" data-preset-id="${escapeHtml(preset.id)}" type="button" ${installed || busy(busyKey) ? "disabled" : ""}>
        ${installed ? "已启用" : busy(busyKey) ? "启用中" : "启用预设"}
      </button>
    </article>
  `;
}

function renderSkillDetailPanel() {
  const skill = viewState.skillDetail;
  if (!skill) return "";
  const isBuiltin = skill.source === "built-in";
  const isEditing = viewState.skillEditing;
  return `
    <div class="skill-detail-panel">
      <div class="skill-detail-header">
        <button class="icon-button subtle" data-action="skill-back" title="返回能力列表" type="button">← 返回</button>
        <div>
          <h3>${escapeHtml(skill.name)}</h3>
          <span class="badge ${escapeHtml(skill.status)}">${capabilityStatusLabel(skill.status)}</span>
          ${isBuiltin ? `<span class="badge builtin">内置</span>` : ""}
        </div>
      </div>
      <div class="skill-detail-meta">
        <div><strong>来源</strong><span>${escapeHtml(skill.source || "—")}</span></div>
        ${skill.agents?.length ? `<div><strong>适用 Agent</strong><span>${skill.agents.map((agent) => escapeHtml(agent)).join(", ")}</span></div>` : ""}
        ${skill.use_cases?.length ? `<div><strong>适用场景</strong><span>${skill.use_cases.slice(0, 3).map((useCase) => escapeHtml(useCase)).join(" / ")}</span></div>` : ""}
      </div>
      <div class="skill-detail-content">
        <strong>内容</strong>
        ${
          isEditing
            ? `<textarea id="skill-edit-textarea" class="skill-edit-textarea" rows="14">${escapeHtml(skill.content)}</textarea>`
            : `<pre>${escapeHtml(skill.content)}</pre>`
        }
      </div>
      <div class="skill-detail-actions">
        ${
          isBuiltin
            ? `<span class="muted-hint">内置 Skill 仅供查看，不可编辑或删除。</span>`
            : isEditing
              ? `
              <button class="button primary compact-button" data-action="skill-save" type="button">保存</button>
              <button class="button secondary compact-button" data-action="skill-cancel" type="button">取消</button>
            `
              : `
              <button class="button primary compact-button" data-action="skill-edit" type="button">编辑</button>
              <button class="button danger compact-button" data-action="skill-delete" type="button">删除</button>
            `
        }
      </div>
    </div>
  `;
}

function renderCapabilityGroup(group) {
  const items = group.items || [];
  return `
    <section class="capability-group">
      <div class="capability-group-head">
        <h3>${escapeHtml(group.label)}</h3>
        <span>${escapeHtml(items.length)} 项</span>
      </div>
      <div class="capability-list">
        ${items.length ? items.map(renderCapabilityCard).join("") : `<div class="empty-mini">暂无能力</div>`}
      </div>
    </section>
  `;
}

function renderCapabilityCard(item) {
  return `
    <article class="capability-card ${escapeHtml(item.kind || "tool")}">
      <div class="capability-card-head">
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(capabilityKindLabel(item.kind))}</span>
        </div>
        <span class="badge ${escapeHtml(item.status)}">${capabilityStatusLabel(item.status)}</span>
      </div>
      <p>${escapeHtml(item.description)}</p>
      <div class="capability-meta">
        ${(item.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
      </div>
      <div class="capability-agents">
        ${(item.agents || []).slice(0, 3).map((agent) => `<span>${escapeHtml(agent)}</span>`).join("")}
      </div>
      ${renderCapabilityMarketDetails(item)}
      ${renderMCPFields(item)}
      ${item.kind === "skill" && item.status === "configured" ? `<button class="button secondary compact-button" data-action="skill-detail" data-skill-id="${escapeHtml(item.id)}" type="button" style="margin-top:6px;">预览 / 编辑</button>` : ""}
      ${item.kind === "skill" && item.status === "ready" ? `<button class="button secondary compact-button" data-action="skill-detail" data-skill-id="${escapeHtml(item.id)}" type="button" style="margin-top:6px;">查看详情</button>` : ""}
    </article>
  `;
}

function renderCapabilityMarketDetails(item) {
  const detailGroups = [
    ["适用", item.use_cases || []],
    ["输入", item.inputs || []],
    ["输出", item.outputs || []],
    ["风险", item.risks || []],
    ["配置", [item.source || item.setup_source, item.setup_hint].filter(Boolean)],
  ].filter(([, values]) => values.length);

  if (!detailGroups.length) return "";

  return `
    <div class="capability-market">
      ${detailGroups
        .map(
          ([label, values]) => `
            <div>
              <strong>${escapeHtml(label)}</strong>
              <span>${values.slice(0, 3).map(escapeHtml).join(" / ")}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderMCPFields(item) {
  if (item.kind !== "mcp") return "";
  const server = findMcpServer(item.id);
  const merged = { ...item, ...(server || {}) };
  const preset = findMcpPresetForServer(item.id);
  const runtimeStatus = viewState.mcpConfig?.statusByServer?.[item.id] || {};
  const toolsState = viewState.mcpConfig?.toolsByServer?.[item.id] || {};
  const tools = Array.isArray(toolsState.tools) ? toolsState.tools : [];
  const validation = viewState.mcpConfig?.validationByServer?.[item.id] || [];
  const vChecks = validation.filter((check) => check.id.startsWith("config_") || check.id.startsWith("command_") || check.id.startsWith("env_"));
  const canInspect = merged.status !== "planned";
  const runtimeLabel = runtimeStatus.status || toolsState.status || (merged.enabled === false ? "disabled" : merged.status || "unknown");
  return `
    <div class="mcp-fields">
      <div class="mcp-field"><strong>来源</strong><span>${escapeHtml(merged.source || merged.setup_source || "未配置")}</span></div>
      <div class="mcp-field"><strong>命令</strong><code>${escapeHtml(merged.command || "—")}</code></div>
      ${(merged.args || []).length ? `<div class="mcp-field"><strong>参数</strong><code>${escapeHtml(merged.args.slice(0, 5).join(" "))}</code></div>` : ""}
      ${(merged.env_keys || []).length ? `<div class="mcp-field"><strong>环境变量</strong><span>${merged.env_keys.slice(0, 5).map((key) => escapeHtml(key)).join(", ")}${merged.env_keys.length > 5 ? " ...+" + (merged.env_keys.length - 5) : ""}</span></div>` : ""}
      ${merged.last_used_run_id || runtimeStatus.last_used_run_id ? `<div class="mcp-field"><strong>最近使用</strong><span class="run-link">${escapeHtml(merged.last_used_run_id || runtimeStatus.last_used_run_id)}</span></div>` : ""}
      <div class="mcp-runtime">
        <div>
          <strong>运行态</strong>
          <span class="badge ${escapeHtml(runtimeLabel)}">${escapeHtml(mcpRuntimeLabel(runtimeLabel))}</span>
          ${typeof runtimeStatus.failure_count === "number" ? `<span>${escapeHtml(runtimeStatus.failure_count)} 次失败</span>` : ""}
          ${runtimeStatus.circuit_open_until ? `<span>熔断至 ${escapeHtml(formatTime(runtimeStatus.circuit_open_until))}</span>` : ""}
        </div>
        ${toolsState.cache ? `<div><strong>工具缓存</strong><span>${escapeHtml(mcpCacheLabel(toolsState.cache))}${toolsState.cached_at ? ` · ${escapeHtml(formatTime(toolsState.cached_at))}` : ""}</span></div>` : ""}
        ${toolsState.error || runtimeStatus.last_error ? `<div class="mcp-runtime-error"><strong>错误</strong><span>${escapeHtml(toolsState.error || runtimeStatus.last_error)}</span></div>` : ""}
        ${
          tools.length
            ? `
          <div class="mcp-tool-list">
            ${tools
              .slice(0, 6)
              .map(
                (tool) => `
              <span title="${escapeHtml(tool.description || tool.name || "")}">${escapeHtml(tool.name || "unnamed")}</span>
            `,
              )
              .join("")}
            ${tools.length > 6 ? `<span>+${escapeHtml(tools.length - 6)}</span>` : ""}
          </div>
        `
            : ""
        }
      </div>
      ${merged.setup_hint && merged.status !== "ready" && merged.status !== "configured" ? `<div class="mcp-hint">${escapeHtml(merged.setup_hint)}</div>` : ""}
      <div class="mcp-card-actions">
        ${preset && merged.status === "planned" ? `<button class="button primary compact-button" data-action="install-mcp-preset" data-preset-id="${escapeHtml(preset.id)}" type="button" ${busy(`install-mcp-preset:${preset.id}`) ? "disabled" : ""}>${busy(`install-mcp-preset:${preset.id}`) ? "启用中" : "启用预设"}</button>` : ""}
        <button class="button secondary compact-button" data-action="prepare-mcp-config" data-server-id="${escapeHtml(item.id)}" data-server-name="${escapeHtml(item.name || item.id)}" type="button">${merged.status === "planned" ? "填写配置" : "更新配置"}</button>
        ${canInspect ? `<button class="button secondary compact-button" data-action="validate-mcp" data-server-id="${escapeHtml(item.id)}" type="button">验证配置</button>` : ""}
        ${canInspect ? `<button class="button secondary compact-button" data-action="load-mcp-tools" data-server-id="${escapeHtml(item.id)}" type="button">刷新工具</button>` : ""}
      </div>
      ${vChecks.length ? `<div class="mcp-validation">${vChecks.map((check) => `<div class="mcp-check ${check.status}"><span class="mcp-check-label">${escapeHtml(check.label)}</span><span class="mcp-check-detail">${escapeHtml(check.detail)}</span></div>`).join("")}</div>` : ""}
    </div>
  `;
}

function findMcpServer(serverId) {
  const servers = viewState.mcpConfig?.servers || [];
  return servers.find((server) => server.id === serverId) || null;
}

function findMcpPresetForServer(serverId) {
  const presets = viewState.mcpConfig?.presets || [];
  const found = presets.find((preset) => preset.server_id === serverId);
  if (found) return found;
  const fallbackIds = {
    "mcp.filesystem": "filesystem",
    "mcp.docs": "docs",
    "mcp.memory": "memory",
    "mcp.sequential-thinking": "sequential-thinking",
    "mcp.github": "github",
  };
  return fallbackIds[serverId] ? { id: fallbackIds[serverId], server_id: serverId } : null;
}

function mcpRuntimeLabel(status) {
  const labels = {
    ready: "可用",
    configured: "已配置",
    passed: "可用",
    warning: "有警告",
    failed: "失败",
    circuit_open: "已熔断",
    planned: "待接入",
    disabled: "已禁用",
    unknown: "未知",
  };
  return labels[status] || status || "未知";
}

function mcpCacheLabel(cache) {
  const labels = {
    hit: "命中缓存",
    miss: "实时刷新",
    stale: "过期缓存",
  };
  return labels[cache] || cache || "未缓存";
}
