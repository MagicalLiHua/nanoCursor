import React, { useEffect, useState, useCallback } from "react";
import {
  X, User, Palette, Server, FolderOpen, Plug, Zap, Keyboard,
  Plus, Trash2, RefreshCw, Check, AlertCircle, ExternalLink,
} from "lucide-react";
import useStore from "../../store/index.js";
import { getApiClient } from "../../core/sharedApi.js";
import {
  loadMcpConfigBundle,
  installMcpPreset,
  loadSkillDetail,
  saveSkillContent,
  importCustomSkill,
  deleteSkill,
} from "../../actions/capabilityActions.js";

const SECTIONS = [
  { id: "profile", label: "个人资料", icon: User },
  { id: "appearance", label: "外观", icon: Palette },
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

/* ── Appearance ── */

function AppearanceSection() {
  const [theme, setTheme] = React.useState(() => localStorage.getItem("nc_theme") || "light");

  const handleThemeChange = (newTheme) => {
    setTheme(newTheme);
    localStorage.setItem("nc_theme", newTheme);
    document.documentElement.setAttribute("data-theme", newTheme);
  };

  return (
    <div className="settings-section-content">
      <h3>外观</h3>
      <p className="settings-description">自定义界面外观</p>

      <div className="settings-field">
        <label>主题</label>
        <div className="settings-theme-options">
          <button
            className={`theme-option ${theme === "light" ? "active" : ""}`}
            onClick={() => handleThemeChange("light")}
            type="button"
          >
            <div className="theme-preview light" />
            <span>浅色</span>
          </button>
          <button
            className={`theme-option ${theme === "dark" ? "active" : ""}`}
            onClick={() => handleThemeChange("dark")}
            type="button"
          >
            <div className="theme-preview dark" />
            <span>深色</span>
          </button>
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
  const setState = useStore((s) => s.setState);
  const showToast = useStore((s) => s.showToast);

  useEffect(() => {
    useStore.getState().loadRecentProjects?.();
  }, []);

  const handleOpen = () => {
    openWorkspace();
  };

  return (
    <div className="settings-section-content">
      <h3>工作区</h3>
      <p className="settings-description">管理项目工作目录</p>

      <div className="settings-field">
        <label>当前工作路径</label>
        <div className="workspace-path-row">
          <input
            value={workspaceInput || workspaceDir || ""}
            onChange={(e) => setState({ workspaceInput: e.target.value })}
            placeholder="/Users/you/project"
          />
          <button className="button compact-button" onClick={handleOpen} type="button">打开</button>
        </div>
      </div>

      {recentProjects?.length > 0 && (
        <div className="settings-field">
          <label>最近项目</label>
          <div className="recent-projects-list">
            {recentProjects.map((item) => (
              <button
                key={item.path}
                className="recent-project-item"
                onClick={() => {
                  setState({ workspaceInput: item.path });
                  showToast?.({ title: "已选择", content: item.path, kind: "info" });
                }}
                type="button"
              >
                <FolderOpen size={14} />
                <span>{item.name || item.path}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── LLM Config ── */

function LlmSection() {
  return (
    <div className="settings-section-content">
      <h3>LLM 配置</h3>
      <p className="settings-description">配置大语言模型 API 连接</p>

      <div className="settings-field">
        <label>API 端点</label>
        <input
          defaultValue="http://127.0.0.1:8100"
          placeholder="http://127.0.0.1:8100"
          readOnly
        />
        <span className="settings-hint">在 .env 文件中配置 API Key 和模型</span>
      </div>

      <div className="settings-field">
        <label>支持的提供商</label>
        <div className="settings-info-list">
          <div className="settings-info-item">
            <span className="provider-name">DeepSeek</span>
            <span className="settings-hint">DEEPSEEK_API_KEY</span>
          </div>
          <div className="settings-info-item">
            <span className="provider-name">MiniMax</span>
            <span className="settings-hint">MINIMAX_API_KEY</span>
          </div>
          <div className="settings-info-item">
            <span className="provider-name">OpenAI</span>
            <span className="settings-hint">OPENAI_API_KEY</span>
          </div>
          <div className="settings-info-item">
            <span className="provider-name">Anthropic</span>
            <span className="settings-hint">ANTHROPIC_API_KEY</span>
          </div>
          <div className="settings-info-item">
            <span className="provider-name">Ollama (本地)</span>
            <span className="settings-hint">OLLAMA_BASE_URL</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── MCP Servers ── */

function McpSection() {
  const [loading, setLoading] = useState(true);
  const [servers, setServers] = useState([]);
  const [presets, setPresets] = useState([]);
  const [installing, setInstalling] = useState(null);
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
                {servers.map((server) => (
                  <div key={server.id || server.name} className="mcp-server-item">
                    <div className="mcp-server-info">
                      <span className="mcp-server-name">{server.id || server.name}</span>
                      <span className="mcp-server-cmd">{server.command || ""}</span>
                    </div>
                    <span className={`mcp-status-dot ${server.status === "running" ? "running" : "stopped"}`} />
                  </div>
                ))}
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
                      disabled={installing === preset.id}
                      type="button"
                    >
                      {installing === preset.id ? "安装中..." : "安装"}
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
  const [error, setError] = useState("");

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const api = getApiClient();
      const result = await api.fetchJson("/api/capabilities/skills");
      setSkills(Array.isArray(result?.skills) ? result.skills : []);
    } catch {
      // ignore
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
    try {
      const api = getApiClient();
      await deleteSkill({ requestJson: api.requestJson, skillId });
      await loadSkills();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleImport = async () => {
    if (!importName.trim()) return;
    try {
      const api = getApiClient();
      await importCustomSkill({ requestJson: api.requestJson, name: importName.trim() });
      setImportName("");
      await loadSkills();
    } catch (e) {
      setError(e.message);
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
                      </div>
                      <div className="skill-actions">
                        <button className="icon-button subtle" onClick={() => handleEdit(skill.id || skill.name)} title="编辑" type="button">
                          <Zap size={14} />
                        </button>
                        <button className="icon-button subtle danger" onClick={() => handleDelete(skill.id || skill.name)} title="删除" type="button">
                          <Trash2 size={14} />
                        </button>
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
    if (activeSection === "appearance") return <AppearanceSection />;
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
