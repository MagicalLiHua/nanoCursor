import {
  deleteSkill as deleteSkillAction,
  importCustomSkill as importCustomSkillAction,
  installMcpPreset as installMcpPresetAction,
  loadCapabilities as loadCapabilitiesAction,
  loadMcpConfigBundle,
  loadMcpTools as loadMcpToolsAction,
  loadSkillDetail as loadSkillDetailAction,
  saveMcpServerConfig as saveMcpServerConfigAction,
  saveSkillContent as saveSkillContentAction,
  validateMcpConfig as validateMcpConfigAction,
} from "../actions/capabilityActions.js";
import {
  normalizeMcpConfig as normalizeMcpConfigPayload,
  parseMcpArgs,
  parseMcpEnvKeys,
} from "../services/mcpConfig.js";

export function createCapabilityController({
  state,
  render,
  requestJson,
  fetchJson,
  withBusyAction,
  showToast,
  addTimelineEvent,
  loadWorkspaceOverview,
  inferLocalCapabilityRecommendation,
}) {
  function normalizeMcpConfig(raw = {}, status = null, presetsPayload = null) {
    return normalizeMcpConfigPayload(raw, {
      status,
      presetsPayload,
      previous: state.mcpConfig || {},
    });
  }

  async function importCustomSkill() {
    const name = document.querySelector("#skill-name-input")?.value.trim();
    const content = document.querySelector("#skill-content-input")?.value.trim() || "";

    if (!name) {
      addTimelineEvent({
        type: "error",
        title: "Skill 名称为空",
        content: "请先填写自定义 Skill 名称。",
      });
      return;
    }

    try {
      const result = await importCustomSkillAction({ requestJson, name, content });
      state.capabilityHub = result.hub || state.capabilityHub;
      state.rightTab = "capabilities";
      addTimelineEvent({
        type: "capability_used",
        title: "自定义 Skill 已导入",
        content: `${name} 已写入当前项目的 .nanocursor/skills。`,
        payload: {
          capability_trace: {
            capability_name: name,
            capability_id: result.skill?.id || `skill.${name}`,
            kind: "skill",
            agent: "Lead",
          },
        },
      });
    } catch (error) {
      addTimelineEvent({
        type: "error",
        title: "导入 Skill 失败",
        content: error.message,
      });
    }
  }

  async function loadCapabilities() {
    const result = await loadCapabilitiesAction({ fetchJson });
    if (result) {
      state.capabilityHub = result;
    }
    state.capabilityRecommendation = inferLocalCapabilityRecommendation(state.prompt);
    render();
  }

  async function loadMcpConfig() {
    const bundle = await loadMcpConfigBundle({ fetchJson });
    state.mcpConfig = bundle
      ? normalizeMcpConfig(bundle.config, bundle.status, bundle.presets)
      : normalizeMcpConfig(state.mcpConfig || {});
    render();
  }

  async function installMcpPreset(presetId) {
    if (!presetId) return;
    await withBusyAction(`install-mcp-preset:${presetId}`, async () => {
      try {
        const result = await installMcpPresetAction({ requestJson, presetId });
        await loadMcpConfig();
        await loadCapabilities();
        await loadWorkspaceOverview();
        addTimelineEvent({
          type: "capability_used",
          title: "MCP 预设已启用",
          content: `${result.preset?.name || presetId} 已写入当前项目配置。`,
        });
        showToast("success", "MCP 预设已启用");
      } catch (error) {
        addTimelineEvent({
          type: "error",
          title: "启用 MCP 预设失败",
          content: error.message,
        });
        showToast("error", "启用 MCP 预设失败");
      }
    });
  }

  async function validateMcpConfig(serverId) {
    try {
      const checks = await validateMcpConfigAction({ requestJson, serverId });
      state.mcpConfig = state.mcpConfig || {};
      state.mcpConfig.validationByServer = {
        ...(state.mcpConfig.validationByServer || {}),
        [serverId || "all"]: checks,
      };
    } catch {
      state.mcpConfig = state.mcpConfig || {};
      state.mcpConfig.validationByServer = {
        ...(state.mcpConfig.validationByServer || {}),
        [serverId || "all"]: [],
      };
    }
    render();
  }

  async function loadMcpTools(serverId, refresh = true) {
    if (!serverId) return;
    state.mcpConfig = normalizeMcpConfig(state.mcpConfig || {});
    state.mcpConfig.toolsByServer = {
      ...(state.mcpConfig.toolsByServer || {}),
      [serverId]: {
        ...(state.mcpConfig.toolsByServer?.[serverId] || {}),
        loading: true,
        error: "",
      },
    };
    render();

    try {
      const { tools: result, status } = await loadMcpToolsAction({ fetchJson, serverId, refresh });
      state.mcpConfig.toolsByServer = {
        ...(state.mcpConfig.toolsByServer || {}),
        [serverId]: result,
      };
      if (status) {
        state.mcpConfig.statusByServer = {
          ...(state.mcpConfig.statusByServer || {}),
          [serverId]: status,
        };
      }
      if (result.ok) {
        addTimelineEvent({
          type: "capability_used",
          title: "MCP 工具已刷新",
          content: `${serverId} 暴露 ${result.tools?.length || 0} 个工具。`,
        });
      }
    } catch (error) {
      state.mcpConfig.toolsByServer = {
        ...(state.mcpConfig.toolsByServer || {}),
        [serverId]: {
          ok: false,
          tools: [],
          error: error.message,
        },
      };
      addTimelineEvent({
        type: "error",
        title: "刷新 MCP 工具失败",
        content: error.message,
      });
    }
    render();
  }

  async function saveMcpServerConfig() {
    const serverId = document.querySelector("#mcp-server-name-input")?.value.trim();
    const command = document.querySelector("#mcp-command-input")?.value.trim();
    const args = parseMcpArgs(document.querySelector("#mcp-args-input")?.value || "");
    const envKeys = parseMcpEnvKeys(document.querySelector("#mcp-env-input")?.value || "");

    if (!serverId || !command) {
      addTimelineEvent({
        type: "error",
        title: "MCP 配置不完整",
        content: "请填写 server 名称和启动命令。",
      });
      return;
    }

    try {
      const result = await saveMcpServerConfigAction({ requestJson, serverId, command, args, envKeys });
      await loadMcpConfig();
      await loadCapabilities();
      await loadWorkspaceOverview();
      addTimelineEvent({
        type: "capability_used",
        title: "MCP Server 已配置",
        content: `${result.server?.id || serverId} 已写入 .nanocursor/mcp.json。`,
        payload: {
          capability_trace: {
            capability_name: result.server?.name || serverId,
            capability_id: result.server?.id || `mcp.${serverId}`,
            kind: "mcp",
            agent: "Lead",
          },
        },
      });
    } catch (error) {
      addTimelineEvent({
        type: "error",
        title: "保存 MCP 配置失败",
        content: error.message,
      });
    }
    render();
  }

  async function loadSkillDetail(skillId) {
    try {
      state.skillDetail = await loadSkillDetailAction({ fetchJson, skillId });
    } catch (error) {
      state.skillDetail = null;
      addTimelineEvent({ type: "error", title: "获取 Skill 详情失败", content: error.message });
    }
    state.skillEditing = false;
    state.rightTab = "capabilities";
    render();
  }

  async function saveSkillContent() {
    const skillId = state.skillDetail?.id;
    const content = document.querySelector("#skill-edit-textarea")?.value;
    if (!skillId || content == null) return;
    try {
      const result = await saveSkillContentAction({ requestJson, skillId, content });
      state.skillDetail = result;
      state.skillEditing = false;
      addTimelineEvent({ type: "capability_used", title: "Skill 已更新", content: `${result.name} 内容已保存。` });
      loadCapabilities();
      loadWorkspaceOverview();
    } catch (error) {
      addTimelineEvent({ type: "error", title: "保存 Skill 失败", content: error.message });
    }
    render();
  }

  async function deleteSkill(skillId) {
    if (!window.confirm("确认删除此 Skill？此操作不可撤销。")) return;
    try {
      await deleteSkillAction({ requestJson, skillId });
      state.skillDetail = null;
      state.skillEditing = false;
      addTimelineEvent({ type: "capability_used", title: "Skill 已删除", content: `${skillId} 已从工作区移除。` });
      loadCapabilities();
      loadWorkspaceOverview();
    } catch (error) {
      addTimelineEvent({ type: "error", title: "删除 Skill 失败", content: error.message });
    }
    render();
  }

  function cancelSkillEdit() {
    state.skillEditing = false;
    render();
  }

  return {
    cancelSkillEdit,
    deleteSkill,
    importCustomSkill,
    installMcpPreset,
    loadCapabilities,
    loadMcpConfig,
    loadMcpTools,
    loadSkillDetail,
    normalizeMcpConfig,
    saveMcpServerConfig,
    saveSkillContent,
    validateMcpConfig,
  };
}
