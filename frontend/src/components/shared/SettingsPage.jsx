import React, { useEffect, useState, useCallback } from "react";
import {
  X, User, Server, FolderOpen, Plug, Zap, Keyboard,
  Plus, Trash2, RefreshCw, Check, AlertCircle, ExternalLink,
} from "lucide-react";
import useStore from "../../store/index.js";
import { getApiClient } from "../../core/sharedApi.js";
import {
  loadMcpConfigBundle,
  installMcpPreset,
  loadMcpTools,
  probeMcpServer,
  setMcpServerEnabled,
  deleteMcpServer,
  loadSkills as loadSkillRegistry,
  loadSkillDetail,
  saveSkillContent,
  importCustomSkill,
  deleteSkill,
  setSkillEnabled,
  previewGitHubSkillImport,
  importGitHubSkill,
} from "../../actions/capabilityActions.js";

const SECTIONS = [
  { id: "profile", label: "个人资料", icon: User },
  { id: "workspace", label: "工作区", icon: FolderOpen },
  { id: "llm", label: "LLM 配置", icon: Server },
  { id: "mcp", label: "MCP 服务器", icon: Plug },
  { id: "skills", label: "技能管理", icon: Zap },
  { id: "shortcuts", label: "快捷键", icon: Keyboard },
];

/* ── Profile ── */

function ProfileSection() {
  const [userName, setUserName] = React.useState(() => localStorage.getItem("nc_user_name") || "User");
  const [userAvatar, setUserAvatar] = React.useState(() => localStorage.getItem("nc_user_avatar") || "");
  const [editName, setEditName] = React.useState(userName);

  const handleSave = () => {
    if (editName.trim()) {
      setUserName(editName.trim());
      localStorage.setItem("nc_user_name", editName.trim());
    }
  };

  const handleAvatarChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      setUserAvatar(dataUrl);
      localStorage.setItem("nc_user_avatar", dataUrl);
    };
    reader.readAsDataURL(file);
  };

  const initial = userName.charAt(0).toUpperCase();

  return (
    <div className="settings-section-content">
      <h3>个人资料</h3>
      <p className="settings-description">自定义您的个人头像和名称</p>

      <div className="settings-field">
        <label>头像</label>
        <div className="settings-avatar-row">
          <div className="settings-avatar">
            {userAvatar ? <img src={userAvatar} alt={userName} /> : <span>{initial}</span>}
          </div>
          <label className="settings-avatar-upload">
            <input type="file" accept="image/*" onChange={handleAvatarChange} />
            <span>更换头像</span>
          </label>
        </div>
      </div>

      <div className="settings-field">
        <label>名称</label>
        <div className="settings-name-row">
          <input
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSave()}
            placeholder="输入您的名称"
          />
          <button className="button compact-button" onClick={handleSave} type="button">保存</button>
        </div>
      </div>
    </div>
  );
}

/* ── Workspace ── */

function WorkspaceSection() {
  const workspaceDir = useStore((s) => s.workspaceDir);
  const workspaceInput = useStore((s) => s.workspaceInput);
  const recentProjects = useStore((s) => s.recentProjects);
  const openWorkspace = useStore((s) => s.openWorkspace);
  const showToast = useStore((s) => s.showToast);
  const [pathDialogOpen, setPathDialogOpen] = useState(false);
  const [pathDraft, setPathDraft] = useState("");

  useEffect(() => {
    useStore.getState().loadRecentProjects?.();
  }, []);

  const handleOpenDialog = () => {
    setPathDraft(workspaceInput || workspaceDir || "");
    setPathDialogOpen(true);
  };

  const handleConfirmPath = async (event) => {
    event?.preventDefault();
    const nextPath = pathDraft.trim();
    if (!nextPath) {
      showToast?.({ title: "路径为空", content: "请输入要打开的项目目录。", kind: "warning" });
      return;
    }
    const opened = await openWorkspace(nextPath);
    if (opened) {
      setPathDialogOpen(false);
    }
  };

  const uniqueRecentProjects = Array.from(
    new Map((recentProjects || []).filter((item) => item?.path).map((item) => [item.path, item])).values()
  );
  const currentPath = workspaceDir || workspaceInput || "";

  return (
    <div className="settings-section-content">
      <h3>工作区</h3>
      <p className="settings-description">管理项目工作目录</p>

      <div className="settings-field">
        <label>当前工作路径</label>
        <div className="workspace-path-row">
          <div className="workspace-path-display" title={currentPath || "未设置工作路径"}>
            {currentPath || "未设置工作路径"}
          </div>
          <button className="button compact-button" onClick={handleOpenDialog} type="button">设置工作路径</button>
        </div>
      </div>

      {uniqueRecentProjects.length > 0 && (
        <div className="settings-field">
          <label>最近项目</label>
          <div className="recent-projects-list">
            {uniqueRecentProjects.map((item) => (
              <button
                key={item.path}
                className="recent-project-item"
                onClick={() => {
                  openWorkspace(item.path);
                }}
                type="button"
              >
                <FolderOpen size={14} />
                <span title={item.path}>
                  <strong>{item.name || item.path}</strong>
                  <small>{item.path}</small>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {pathDialogOpen && (
        <div className="workspace-path-dialog-backdrop" onClick={() => setPathDialogOpen(false)}>
          <form
            className="workspace-path-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="workspace-path-dialog-title"
            onClick={(event) => event.stopPropagation()}
            onSubmit={handleConfirmPath}
          >
            <div className="workspace-path-dialog-header">
              <div>
                <h4 id="workspace-path-dialog-title">设置工作路径</h4>
                <p>输入项目目录的绝对路径，确认后会切换当前会话的工作区。</p>
              </div>
              <button className="icon-button" type="button" aria-label="关闭" onClick={() => setPathDialogOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <input
              autoFocus
              value={pathDraft}
              onChange={(event) => setPathDraft(event.target.value)}
              placeholder="/Users/you/project"
            />
            <div className="workspace-path-dialog-actions">
              <button className="button secondary compact-button" type="button" onClick={() => setPathDialogOpen(false)}>取消</button>
              <button className="button compact-button" type="submit">确定</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

/* ── LLM Config ── */

const PROVIDER_OPTIONS = [
  { id: "deepseek", label: "DeepSeek", key: "DEEPSEEK_API_KEY" },
  { id: "anthropic", label: "Anthropic", key: "ANTHROPIC_API_KEY" },
  { id: "openai", label: "OpenAI", key: "OPENAI_API_KEY" },
  { id: "minimax", label: "MiniMax", key: "MINIMAX_API_KEY" },
  { id: "ollama", label: "Ollama 本地", key: "OLLAMA_BASE_URL" },
];

function ProviderStatus({ providers = {}, activeProvider = "" }) {
  return (
    <div className="llm-provider-grid">
      {PROVIDER_OPTIONS.map((provider) => {
        const status = providers[provider.id] || {};
        const configured = Boolean(status.has_key);
        return (
          <div key={provider.id} className={`llm-provider-card ${activeProvider === provider.id ? "active" : ""}`}>
            <div>
              <strong>{provider.label}</strong>
              <span>{status.model || "未设置模型"}</span>
            </div>
            <span className={`settings-pill ${configured ? "ok" : "muted"}`}>
              {configured ? "已配置" : "未配置"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function LlmSection() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState(null);
  const [checks, setChecks] = useState([]);
  const [form, setForm] = useState({
    provider: "deepseek",
    default_model: "",
    base_url: "",
    api_key: "",
    temperature: 0.2,
    max_tokens: 8192,
  });
  const showToast = useStore((s) => s.showToast);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const api = getApiClient();
      const [configData, settingsData, validation] = await Promise.all([
        api.fetchJson("/api/config"),
        api.fetchJson("/api/workspace/settings/effective"),
        api.requestJson("/api/workspace/settings/validate", { method: "POST" }),
      ]);
      const model = settingsData?.model || {};
      const detectedProvider = model.provider || Object.entries(configData?.llm_providers || {}).find(([, value]) => value?.has_key)?.[0] || "deepseek";
      const providerConfig = configData?.llm_providers?.[detectedProvider] || {};
      setConfig(configData);
      setChecks(validation?.checks || []);
      setForm({
        provider: detectedProvider,
        default_model: model.default_model || providerConfig.model || "",
        base_url: model.base_url || providerConfig.base_url || "",
        api_key: "",
        temperature: Number(model.temperature ?? 0.2),
        max_tokens: Number(model.max_tokens ?? 8192),
      });
    } catch (error) {
      showToast?.({ title: "LLM 配置加载失败", content: error.message, kind: "error" });
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const activeStatus = config?.llm_providers?.[form.provider] || {};
  const selectedProvider = PROVIDER_OPTIONS.find((item) => item.id === form.provider);

  const updateField = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const saveConfig = async () => {
    setSaving(true);
    try {
      const api = getApiClient();
      const modelSettings = {
        provider: form.provider,
        default_model: form.default_model.trim(),
        base_url: form.base_url.trim(),
        temperature: Number(form.temperature),
        max_tokens: Number(form.max_tokens),
      };
      if (form.api_key.trim()) {
        modelSettings.api_key = form.api_key.trim();
      }
      await api.requestJson("/api/workspace/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: { model: modelSettings } }),
      });
      showToast?.({ title: "LLM 配置已保存", content: "新的配置会在下一次模型调用时生效。", kind: "success" });
      await loadData();
    } catch (error) {
      showToast?.({ title: "保存失败", content: error.message, kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-section-content">
      <div className="settings-section-header">
        <div>
          <h3>LLM 配置</h3>
          <p className="settings-description">配置当前工作区使用的大语言模型连接</p>
        </div>
        <button className="icon-button subtle" onClick={loadData} type="button" title="刷新配置">
          <RefreshCw size={16} />
        </button>
      </div>

      {loading ? <div className="settings-loading">正在读取配置...</div> : null}

      <ProviderStatus providers={config?.llm_providers || {}} activeProvider={form.provider} />

      <div className="settings-field">
        <label htmlFor="llm-provider">模型提供商</label>
        <select
          id="llm-provider"
          value={form.provider}
          onChange={(e) => {
            const provider = e.target.value;
            const providerStatus = config?.llm_providers?.[provider] || {};
            setForm((current) => ({
              ...current,
              provider,
              default_model: current.default_model || providerStatus.model || "",
              base_url: providerStatus.base_url || current.base_url || "",
              api_key: "",
            }));
          }}
        >
          {PROVIDER_OPTIONS.map((provider) => (
            <option key={provider.id} value={provider.id}>{provider.label}</option>
          ))}
        </select>
      </div>

      <div className="settings-field">
        <label htmlFor="llm-model">默认模型</label>
        <input
          id="llm-model"
          value={form.default_model}
          onChange={(e) => updateField("default_model", e.target.value)}
          placeholder="例如 deepseek-chat / claude-sonnet-4-6"
        />
      </div>

      <div className="settings-field">
        <label htmlFor="llm-base-url">Base URL</label>
        <input
          id="llm-base-url"
          value={form.base_url}
          onChange={(e) => updateField("base_url", e.target.value)}
          placeholder="留空则使用提供商默认地址"
        />
      </div>

      <div className="settings-field">
        <label htmlFor="llm-api-key">API Key</label>
        <input
          id="llm-api-key"
          type="password"
          value={form.api_key}
          onChange={(e) => updateField("api_key", e.target.value)}
          placeholder={activeStatus.has_key ? "已配置，输入新 key 可覆盖" : selectedProvider?.key || "API Key"}
          autoComplete="off"
        />
        <span className="settings-hint">
          出于安全考虑，已保存的 key 不会回显；当前状态：{activeStatus.has_key ? "已配置" : "未配置"}。
        </span>
      </div>

      <div className="settings-two-col">
        <div className="settings-field">
          <label htmlFor="llm-temperature">Temperature</label>
          <input
            id="llm-temperature"
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={form.temperature}
            onChange={(e) => updateField("temperature", e.target.value)}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="llm-max-tokens">Max Tokens</label>
          <input
            id="llm-max-tokens"
            type="number"
            min="512"
            step="512"
            value={form.max_tokens}
            onChange={(e) => updateField("max_tokens", e.target.value)}
          />
        </div>
      </div>

      {checks.length ? (
        <div className="settings-field">
          <label>配置检查</label>
          <div className="settings-check-list">
            {checks.map((check) => (
              <div key={check.id} className={`settings-check-item ${check.status}`}>
                {check.status === "passed" ? <Check size={14} /> : <AlertCircle size={14} />}
                <span>{check.message}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="settings-actions">
        <button className="button compact-button" onClick={loadData} type="button">重新校验</button>
        <button className="button primary compact-button" onClick={saveConfig} disabled={saving} type="button">
          {saving ? "保存中" : "保存配置"}
        </button>
      </div>
    </div>
  );
}

/* ── MCP Servers ── */

function McpSection() {
  const [loading, setLoading] = useState(true);
  const [servers, setServers] = useState([]);
  const [presets, setPresets] = useState([]);
  const [toolsByServer, setToolsByServer] = useState({});
  const [installing, setInstalling] = useState(null);
  const [busyServer, setBusyServer] = useState(null);
  const [error, setError] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const api = getApiClient();
      const bundle = await loadMcpConfigBundle({ fetchJson: api.fetchJson });
      if (bundle) {
        const serverList = bundle.config?.servers || bundle.status?.servers || {};
        setServers(Array.isArray(serverList) ? serverList : Object.entries(serverList).map(([id, cfg]) => ({ id, ...cfg })));
        setPresets(bundle.presets?.presets || []);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleInstallPreset = async (presetId) => {
    setInstalling(presetId);
    try {
      const api = getApiClient();
      await installMcpPreset({ requestJson: api.requestJson, presetId });
      await loadData();
    } catch (e) {
      setError(e.message);
    } finally {
      setInstalling(null);
    }
  };

  const handleProbe = async (serverId) => {
    setBusyServer(serverId);
    setError("");
    try {
      const api = getApiClient();
      await probeMcpServer({ requestJson: api.requestJson, serverId });
      const tools = await loadMcpTools({ fetchJson: api.fetchJson, serverId, refresh: true });
      setToolsByServer((prev) => ({ ...prev, [serverId]: tools.tools || {} }));
      await loadData();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyServer(null);
    }
  };

  const handleToggle = async (serverId, enabled) => {
    setBusyServer(serverId);
    setError("");
    try {
      const api = getApiClient();
      await setMcpServerEnabled({ requestJson: api.requestJson, serverId, enabled });
      await loadData();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyServer(null);
    }
  };

  const handleDelete = async (serverId) => {
    setBusyServer(serverId);
    setError("");
    try {
      const api = getApiClient();
      await deleteMcpServer({ requestJson: api.requestJson, serverId });
      setToolsByServer((prev) => {
        const next = { ...prev };
        delete next[serverId];
        return next;
      });
      await loadData();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyServer(null);
    }
  };

  return (
    <div className="settings-section-content">
      <div className="settings-section-header">
        <div>
          <h3>MCP 服务器</h3>
          <p className="settings-description">管理 Model Context Protocol 服务器</p>
        </div>
        <button className="icon-button subtle" onClick={loadData} title="刷新" type="button">
          <RefreshCw size={14} />
        </button>
      </div>

      {error && (
        <div className="settings-error">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="settings-loading">加载中...</div>
      ) : (
        <>
          <div className="settings-field">
            <label>已安装</label>
            {servers.length > 0 ? (
              <div className="mcp-server-list">
                {servers.map((server) => {
                  const serverId = server.id || server.name;
                  const runtimeTools = toolsByServer[serverId]?.tools || toolsByServer[serverId]?.catalog || [];
                  const toolSummary = runtimeTools.length
                    ? `${runtimeTools.length} tools`
                    : server.last_tools_count
                      ? `${server.last_tools_count} tools`
                      : "";
                  const status = server.enabled === false ? "disabled" : (server.health || server.status || "unknown");
                  return (
                    <div key={serverId} className="mcp-server-item">
                      <div className="mcp-server-info">
                        <span className="mcp-server-name">{serverId}</span>
                        <span className="mcp-server-cmd">{server.command || "未配置命令"}</span>
                        <span className="mcp-preset-desc">
                          {status}
                          {toolSummary ? ` · ${toolSummary}` : ""}
                          {server.last_error ? ` · ${server.last_error}` : ""}
                        </span>
                      </div>
                      <div className="mcp-card-actions">
                        <span className={`mcp-status-dot ${server.status === "ready" ? "running" : "stopped"}`} />
                        <button
                          className="button ghost compact-button"
                          onClick={() => handleProbe(serverId)}
                          disabled={busyServer === serverId || server.enabled === false}
                          type="button"
                        >
                          探测
                        </button>
                        <button
                          className="button ghost compact-button"
                          onClick={() => handleToggle(serverId, server.enabled === false)}
                          disabled={busyServer === serverId || server.config_status !== "configured"}
                          type="button"
                        >
                          {server.enabled === false ? "启用" : "停用"}
                        </button>
                        <button
                          className="icon-button subtle"
                          onClick={() => handleDelete(serverId)}
                          disabled={busyServer === serverId || server.config_status !== "configured"}
                          title="删除"
                          type="button"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                      {runtimeTools.length > 0 && (
                        <div className="mcp-tool-list">
                          {runtimeTools.slice(0, 8).map((tool) => (
                            <span key={`${serverId}-${tool.name || tool.tool}`}>
                              {tool.name || tool.tool}
                              {tool.permission_level ? ` · ${tool.permission_level}` : ""}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="settings-empty">暂无已安装的 MCP 服务器</div>
            )}
          </div>

          {presets.length > 0 && (
            <div className="settings-field">
              <label>可安装的预设</label>
              <div className="mcp-preset-list">
                {presets.map((preset) => (
                  <div key={preset.id} className="mcp-preset-item">
                    <div className="mcp-preset-info">
                      <span className="mcp-preset-name">{preset.name || preset.id}</span>
                      {preset.description && <span className="mcp-preset-desc">{preset.description}</span>}
                    </div>
                    <button
                      className="button compact-button"
                      onClick={() => handleInstallPreset(preset.id)}
                      disabled={installing === preset.id || preset.installed}
                      type="button"
                    >
                      {preset.installed ? "已安装" : installing === preset.id ? "安装中..." : "安装"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── Skills ── */

function SkillsSection() {
  const [loading, setLoading] = useState(true);
  const [skills, setSkills] = useState([]);
  const [editingSkill, setEditingSkill] = useState(null);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [importName, setImportName] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [githubPath, setGithubPath] = useState("");
  const [githubCandidates, setGithubCandidates] = useState([]);
  const [githubBusy, setGithubBusy] = useState(false);
  const [busySkill, setBusySkill] = useState(null);
  const [error, setError] = useState("");

  const loadSkills = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const api = getApiClient();
      const result = await loadSkillRegistry({ fetchJson: api.fetchJson });
      setSkills(Array.isArray(result?.skills) ? result.skills : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  const handleEdit = async (skillId) => {
    try {
      const api = getApiClient();
      const detail = await loadSkillDetail({ fetchJson: api.fetchJson, skillId });
      setEditingSkill(skillId);
      setEditContent(detail?.content || "");
    } catch (e) {
      setError(e.message);
    }
  };

  const handleSave = async () => {
    if (!editingSkill) return;
    setSaving(true);
    try {
      const api = getApiClient();
      await saveSkillContent({ requestJson: api.requestJson, skillId: editingSkill, content: editContent });
      setEditingSkill(null);
      await loadSkills();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (skillId) => {
    setBusySkill(skillId);
    try {
      const api = getApiClient();
      await deleteSkill({ requestJson: api.requestJson, skillId });
      await loadSkills();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusySkill(null);
    }
  };

  const handleImport = async () => {
    if (!importName.trim()) return;
    setBusySkill("import");
    try {
      const api = getApiClient();
      await importCustomSkill({ requestJson: api.requestJson, name: importName.trim() });
      setImportName("");
      await loadSkills();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusySkill(null);
    }
  };

  const handleToggleSkill = async (skillId, enabled) => {
    setBusySkill(skillId);
    try {
      const api = getApiClient();
      await setSkillEnabled({ requestJson: api.requestJson, skillId, enabled });
      await loadSkills();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusySkill(null);
    }
  };

  const handlePreviewGitHub = async () => {
    if (!githubUrl.trim()) return;
    setGithubBusy(true);
    setError("");
    try {
      const api = getApiClient();
      const result = await previewGitHubSkillImport({
        requestJson: api.requestJson,
        repoUrl: githubUrl.trim(),
        path: githubPath.trim(),
      });
      setGithubCandidates(result.candidates || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setGithubBusy(false);
    }
  };

  const handleImportGitHub = async (candidate) => {
    setGithubBusy(true);
    setError("");
    try {
      const api = getApiClient();
      await importGitHubSkill({
        requestJson: api.requestJson,
        repoUrl: githubUrl.trim(),
        path: githubPath.trim(),
        candidateId: candidate.id || candidate.path,
        enabled: candidate.default_enabled,
      });
      setGithubCandidates([]);
      await loadSkills();
    } catch (e) {
      setError(e.message);
    } finally {
      setGithubBusy(false);
    }
  };

  return (
    <div className="settings-section-content">
      <div className="settings-section-header">
        <div>
          <h3>技能管理</h3>
          <p className="settings-description">管理 Agent 可用的技能</p>
        </div>
        <button className="icon-button subtle" onClick={loadSkills} title="刷新" type="button">
          <RefreshCw size={14} />
        </button>
      </div>

      {error && (
        <div className="settings-error">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      {editingSkill ? (
        <div className="settings-field">
          <label>编辑技能: {editingSkill}</label>
          <textarea
            className="skill-editor"
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            rows={16}
            placeholder="输入技能内容 (Markdown)"
          />
          <div className="skill-editor-actions">
            <button className="button compact-button" onClick={handleSave} disabled={saving} type="button">
              {saving ? "保存中..." : "保存"}
            </button>
            <button className="button compact-button secondary" onClick={() => setEditingSkill(null)} type="button">取消</button>
          </div>
        </div>
      ) : (
        <>
          {loading ? (
            <div className="settings-loading">加载中...</div>
          ) : (
            <div className="settings-field">
              <label>已安装技能</label>
              {skills.length > 0 ? (
                <div className="skill-list">
                  {skills.map((skill) => (
                    <div key={skill.id || skill.name} className="skill-item">
                      <div className="skill-info">
                        <span className="skill-name">{skill.name || skill.id}</span>
                        {skill.description && <span className="skill-desc">{skill.description}</span>}
                        <span className="skill-desc">
                          {skill.id}
                          {skill.source?.type ? ` · ${skill.source.type}` : ""}
                          {skill.risk ? ` · ${skill.risk}` : ""}
                          {skill.enabled === false ? " · 已停用" : ""}
                        </span>
                      </div>
                      <div className="skill-actions">
                        {skill.scope !== "builtin" && (
                          <button
                            className="button ghost compact-button"
                            onClick={() => handleToggleSkill(skill.id || skill.name, skill.enabled === false)}
                            disabled={busySkill === (skill.id || skill.name)}
                            type="button"
                          >
                            {skill.enabled === false ? "启用" : "停用"}
                          </button>
                        )}
                        <button className="icon-button subtle" onClick={() => handleEdit(skill.id || skill.name)} title="编辑" type="button">
                          <Zap size={14} />
                        </button>
                        {skill.scope !== "builtin" && (
                          <button
                            className="icon-button subtle danger"
                            onClick={() => handleDelete(skill.id || skill.name)}
                            disabled={busySkill === (skill.id || skill.name)}
                            title="删除"
                            type="button"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="settings-empty">暂无已安装的技能</div>
              )}
            </div>
          )}

          <div className="settings-field">
            <label>导入自定义技能</label>
            <div className="workspace-path-row">
              <input
                value={importName}
                onChange={(e) => setImportName(e.target.value)}
                placeholder="技能名称"
              />
              <button className="button compact-button" onClick={handleImport} disabled={!importName.trim()} type="button">
                <Plus size={14} /> 导入
              </button>
            </div>
          </div>

          <div className="settings-field">
            <label>从 GitHub 导入静态 Skill</label>
            <div className="workspace-path-row">
              <input
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
              />
              <button className="button compact-button" onClick={handlePreviewGitHub} disabled={!githubUrl.trim() || githubBusy} type="button">
                {githubBusy ? "检查中..." : "预览"}
              </button>
            </div>
            <input
              value={githubPath}
              onChange={(e) => setGithubPath(e.target.value)}
              placeholder="可选路径，例如 skills/python-dev"
            />
            {githubCandidates.length > 0 && (
              <div className="skill-list">
                {githubCandidates.map((candidate) => (
                  <div key={`${candidate.id}-${candidate.path}`} className="skill-item">
                    <div className="skill-info">
                      <span className="skill-name">{candidate.name || candidate.id}</span>
                      <span className="skill-desc">
                        {candidate.path || "repo root"} · {candidate.risk}
                        {candidate.findings?.length ? ` · ${candidate.findings.length} 个风险提示` : ""}
                      </span>
                    </div>
                    <button
                      className="button compact-button"
                      onClick={() => handleImportGitHub(candidate)}
                      disabled={githubBusy}
                      type="button"
                    >
                      导入
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ── Shortcuts ── */

function ShortcutsSection() {
  const shortcuts = [
    { keys: "⌘ + K", desc: "打开命令面板" },
    { keys: "Enter", desc: "发送消息" },
    { keys: "Shift + Enter", desc: "换行" },
    { keys: "Esc", desc: "关闭弹窗 / 收起底部面板" },
  ];

  return (
    <div className="settings-section-content">
      <h3>快捷键</h3>
      <p className="settings-description">常用键盘快捷键</p>

      <div className="shortcuts-list">
        {shortcuts.map((s) => (
          <div key={s.keys} className="shortcut-item">
            <kbd>{s.keys}</kbd>
            <span>{s.desc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Main Settings Page ── */

export default function SettingsPage({ onClose }) {
  const [activeSection, setActiveSection] = React.useState("workspace");

  const renderContent = () => {
    if (activeSection === "profile") return <ProfileSection />;
    if (activeSection === "workspace") return <WorkspaceSection />;
    if (activeSection === "llm") return <LlmSection />;
    if (activeSection === "mcp") return <McpSection />;
    if (activeSection === "skills") return <SkillsSection />;
    if (activeSection === "shortcuts") return <ShortcutsSection />;
    return null;
  };

  return (
    <div className="settings-overlay">
      <div className="settings-page">
        <div className="settings-header">
          <h2>设置</h2>
          <button className="icon-button subtle" onClick={onClose} type="button" title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="settings-body">
          <nav className="settings-nav">
            {SECTIONS.map((section) => (
              <button
                key={section.id}
                className={`settings-nav-item ${activeSection === section.id ? "active" : ""}`}
                onClick={() => setActiveSection(section.id)}
                type="button"
              >
                <section.icon size={16} />
                <span>{section.label}</span>
              </button>
            ))}
          </nav>
          <div className="settings-content">
            {renderContent()}
          </div>
        </div>
      </div>
    </div>
  );
}
