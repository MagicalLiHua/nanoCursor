import React from "react";
import {
  MessageSquare, FolderOpen, Settings, User,
  Plus, File, Folder, Search, SquarePen, FolderGit2, Moon, Sun
} from "lucide-react";

function shortPath(path) {
  if (!path) return "";
  const parts = path.split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return ".../" + parts.slice(-2).join("/");
}

function displayRunTitle(run) {
  return run.title || run.prompt || run.id || "未命名会话";
}

function RunList({ runs, currentThreadId, currentConversationId, onSelectRun, onNewSession }) {
  const [query, setQuery] = React.useState("");
  const visibleRuns = (runs || []).filter((run) => {
    const hasPrompt = Boolean(String(run.prompt || "").trim());
    const hasWork = run.status && !["idle", "draft"].includes(run.status);
    if (!hasPrompt && !hasWork) return false;
    const haystack = `${displayRunTitle(run)} ${run.prompt || ""} ${run.id || ""}`.toLowerCase();
    return !query.trim() || haystack.includes(query.trim().toLowerCase());
  });

  return (
    <div className="sidebar-panel-content">
      <div className="sidebar-panel-header">
        <h3>会话</h3>
        <button className="icon-button subtle" onClick={onNewSession} type="button" title="新建会话">
          <Plus size={16} />
        </button>
      </div>
      <div className="sidebar-panel-body">
        <label className="sidebar-search">
          <Search size={14} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话"
          />
        </label>
        {visibleRuns.length ? (
          <div className="run-list">
            {visibleRuns.map((run) => {
              const isActive =
                run.id === currentConversationId ||
                run.id === currentThreadId ||
                run.threadId === currentThreadId;
              const statusClass = run.status || "unknown";
              return (
                <button
                  key={run.id}
                  className={`run-item ${isActive ? "active" : ""} status-${statusClass}`}
                  onClick={() => onSelectRun?.(run.id)}
                  type="button"
                >
                  <div className="run-item-head">
                    <span className={`run-status-dot ${statusClass}`} />
                    <span className="run-item-title" title={displayRunTitle(run)}>{displayRunTitle(run)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="sidebar-empty">暂无会话记录</div>
        )}
      </div>
    </div>
  );
}

function WorkspaceOpenBox({ workspaceDir, workspaceInput, onWorkspaceInputChange, onOpenWorkspace }) {
  return (
    <form className="sidebar-workspace-form" onSubmit={onOpenWorkspace}>
      <label htmlFor="sidebar-workspace-input">工作目录</label>
      <div className="sidebar-workspace-row">
        <input
          id="sidebar-workspace-input"
          value={workspaceInput || workspaceDir || ""}
          onChange={(e) => onWorkspaceInputChange?.(e.target.value)}
          placeholder="/Users/you/project"
        />
        <button className="button compact-button" type="submit">打开</button>
      </div>
    </form>
  );
}

function RecentProjects({ projects = [], onOpenProject }) {
  const visible = projects.filter((item) => item?.path).slice(0, 6);
  if (!visible.length) return null;
  return (
    <section className="sidebar-projects">
      <div className="sidebar-section-title">最近项目</div>
      <div className="sidebar-project-list">
        {visible.map((item) => (
          <button
            key={item.path}
            className="sidebar-project-item"
            onClick={() => onOpenProject?.(item.path)}
            type="button"
          >
            <FolderGit2 size={14} />
            <span title={item.path}>{item.name || shortPath(item.path)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function FileTree({ workspaceFiles, workspaceDir, workspaceInput, recentProjects, onWorkspaceInputChange, onOpenWorkspace }) {
  const visibleFiles = (workspaceFiles || []).filter((file) => {
    const parts = String(file.path || "").split("/").filter(Boolean);
    if (!parts.length) return false;
    const ignored = new Set([".git", ".nanocursor", ".memory", ".tasks", ".pytest_cache", "__pycache__"]);
    return !parts.some((part) => ignored.has(part) || part.endsWith(".pyc"));
  });

  const openProjectPath = (path) => {
    onWorkspaceInputChange?.(path);
    requestAnimationFrame(() => {
      onOpenWorkspace?.({ preventDefault() {} });
    });
  };

  if (!visibleFiles.length) {
    return (
      <div className="sidebar-panel-content">
        <div className="sidebar-panel-header">
          <h3>项目</h3>
          <span className="sidebar-panel-subtitle">{shortPath(workspaceDir)}</span>
        </div>
        <div className="sidebar-panel-body">
          <WorkspaceOpenBox
            workspaceDir={workspaceDir}
            workspaceInput={workspaceInput}
            onWorkspaceInputChange={onWorkspaceInputChange}
            onOpenWorkspace={onOpenWorkspace}
          />
          <RecentProjects projects={recentProjects} onOpenProject={openProjectPath} />
          <div className="sidebar-empty">暂无文件</div>
        </div>
      </div>
    );
  }

  // Build tree structure from workspace files
  const tree = {};
  visibleFiles.forEach((file) => {
    const parts = file.path.split("/").filter(Boolean);
    let current = tree;
    parts.forEach((part, i) => {
      if (i === parts.length - 1) {
        // Last part - this is the file/folder itself
        current[part] = file.is_dir ? {} : null;
      } else {
        // Intermediate directory
        if (!current[part]) {
          current[part] = {};
        }
        if (current[part] === null) {
          current[part] = {}; // Convert file to dir if needed
        }
        current = current[part];
      }
    });
  });

  function renderTree(node, prefix = "", depth = 0) {
    // Sort: folders first, then files
    const entries = Object.entries(node).sort((a, b) => {
      const aIsDir = a[1] !== null;
      const bIsDir = b[1] !== null;
      if (aIsDir && !bIsDir) return -1;
      if (!aIsDir && bIsDir) return 1;
      return a[0].localeCompare(b[0]);
    });

    return entries.map(([name, children]) => {
      const isFile = children === null;
      const fullPath = prefix ? `${prefix}/${name}` : name;
      return (
        <div key={fullPath} className="tree-node">
          <div className={`tree-item ${isFile ? "file" : "folder"}`} style={{ paddingLeft: `${depth * 14 + 8}px` }}>
            <span className="tree-icon">
              {isFile ? <File size={14} /> : <Folder size={14} />}
            </span>
            <span className="tree-name" title={fullPath}>{name}</span>
          </div>
          {!isFile && children && (
            <div className="tree-children">
              {renderTree(children, fullPath, depth + 1)}
            </div>
          )}
        </div>
      );
    });
  }

  return (
    <div className="sidebar-panel-content">
      <div className="sidebar-panel-header">
        <h3>项目</h3>
        <span className="sidebar-panel-subtitle">{shortPath(workspaceDir)}</span>
      </div>
      <div className="sidebar-panel-body">
        <WorkspaceOpenBox
          workspaceDir={workspaceDir}
          workspaceInput={workspaceInput}
          onWorkspaceInputChange={onWorkspaceInputChange}
          onOpenWorkspace={onOpenWorkspace}
        />
        <RecentProjects projects={recentProjects} onOpenProject={openProjectPath} />
        <div className="sidebar-section-title">文件</div>
        <div className="file-tree">
          {renderTree(tree)}
        </div>
      </div>
    </div>
  );
}

function UserProfileMenu({ onOpenSettings }) {
  const [open, setOpen] = React.useState(false);
  const [userName, setUserName] = React.useState(() => localStorage.getItem("nc_user_name") || "User");
  const [userAvatar, setUserAvatar] = React.useState(() => localStorage.getItem("nc_user_avatar") || "");
  const [editing, setEditing] = React.useState(false);
  const [editName, setEditName] = React.useState(userName);
  const menuRef = React.useRef(null);

  React.useEffect(() => {
    function handleClickOutside(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false);
        setEditing(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSaveName = () => {
    if (editName.trim()) {
      setUserName(editName.trim());
      localStorage.setItem("nc_user_name", editName.trim());
    }
    setEditing(false);
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
    <div className="user-profile-menu" ref={menuRef}>
      <button className="user-profile-trigger" onClick={() => setOpen(!open)} type="button">
        <div className="user-avatar-small">
          {userAvatar ? <img src={userAvatar} alt={userName} /> : <span>{initial}</span>}
        </div>
      </button>
      {open && (
        <div className="user-menu-dropdown">
          <div className="user-menu-header">
            <div className="user-menu-avatar-wrapper">
              <div className="user-menu-avatar">
                {userAvatar ? <img src={userAvatar} alt={userName} /> : <span>{initial}</span>}
              </div>
              <label className="user-avatar-upload">
                <input type="file" accept="image/*" onChange={handleAvatarChange} />
                <span>更换头像</span>
              </label>
            </div>
            <div className="user-menu-name-section">
              {editing ? (
                <div className="user-name-edit">
                  <input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSaveName()}
                    autoFocus
                  />
                  <button className="button compact-button" onClick={handleSaveName} type="button">保存</button>
                </div>
              ) : (
                <div className="user-name-display">
                  <span>{userName}</span>
                  <button className="icon-button subtle" onClick={() => { setEditName(userName); setEditing(true); }} type="button" title="编辑名称">
                    <User size={12} />
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="user-menu-divider" />
          <button className="user-menu-item" onClick={() => { onOpenSettings?.(); setOpen(false); }} type="button">
            <Settings size={16} />
            <span>设置</span>
          </button>
        </div>
      )}
    </div>
  );
}

const NAV_ITEMS = [
  { id: "sessions", icon: MessageSquare, label: "会话" },
  { id: "files", icon: FolderOpen, label: "项目" },
];

function ThemeToggle() {
  const [theme, setTheme] = React.useState(() => localStorage.getItem("nc_theme") || "light");
  const isDark = theme === "dark";

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("nc_theme", theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  };

  return (
    <button
      className={`rail-icon theme-toggle ${isDark ? "active" : ""}`}
      onClick={toggleTheme}
      title={isDark ? "切换浅色模式" : "切换深色模式"}
      type="button"
      aria-label={isDark ? "切换浅色模式" : "切换深色模式"}
    >
      {isDark ? <Sun size={19} /> : <Moon size={19} />}
    </button>
  );
}

export default function Sidebar({ state, onToggleSidebar, onNewSession, onSelectRun, onOpenWorkspace, onWorkspaceInputChange, onOpenSettings }) {
  const isCollapsed = state.layout?.sidebarCollapsed;
  const [activePanel, setActivePanel] = React.useState(() => (isCollapsed ? null : "sessions"));
  const workspaceDir = state.projectOverview?.workspace_dir || state.workspaceDir || "";

  React.useEffect(() => {
    function handleOutsidePointerDown(e) {
      if (!activePanel) return;
      if (e.target.closest(".sidebar-v2")) return;
      setActivePanel(null);
      if (!isCollapsed) {
        onToggleSidebar?.();
      }
    }
    document.addEventListener("pointerdown", handleOutsidePointerDown);
    return () => document.removeEventListener("pointerdown", handleOutsidePointerDown);
  }, [activePanel, isCollapsed, onToggleSidebar]);

  const handleNavClick = (id) => {
    if (activePanel === id) {
      setActivePanel(null);
      onToggleSidebar?.();
    } else {
      setActivePanel(id);
      if (isCollapsed) {
        onToggleSidebar?.();
      }
    }
  };

  return (
    <aside className={`sidebar-v2 ${isCollapsed ? "collapsed" : "expanded"}`}>
      {/* Icon Rail - always visible */}
      <div className="sidebar-rail">
        <div className="rail-top">
          <div className="rail-brand" title="nanoCursor">
            <span className="brand-mark-small" aria-hidden="true">
              <span className="brand-mark-letter">n</span>
              <span className="brand-mark-cursor" />
            </span>
          </div>
          <div className="rail-divider" />
          <button
            className="rail-icon rail-primary"
            onClick={onNewSession}
            title="新建会话"
            type="button"
          >
            <SquarePen size={20} />
          </button>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`rail-icon ${activePanel === item.id && !isCollapsed ? "active" : ""}`}
              onClick={() => handleNavClick(item.id)}
              title={item.label}
              type="button"
            >
              <item.icon size={20} />
            </button>
          ))}
        </div>
        <div className="rail-bottom">
          <ThemeToggle />
          <button className="rail-icon" onClick={onOpenSettings} title="设置" type="button">
            <Settings size={20} />
          </button>
          <UserProfileMenu onOpenSettings={onOpenSettings} />
        </div>
      </div>

      {/* Panel - shown when expanded */}
      {!isCollapsed && activePanel && (
        <div className="sidebar-panel">
          {activePanel === "sessions" && (
            <RunList
              runs={state.runs}
              currentThreadId={state.currentThreadId}
              currentConversationId={state.currentConversationId}
              onSelectRun={onSelectRun}
              onNewSession={onNewSession}
            />
          )}
          {activePanel === "files" && (
            <FileTree
              workspaceFiles={state.workspaceFiles || []}
              workspaceDir={workspaceDir}
              workspaceInput={state.workspaceInput}
              recentProjects={state.recentProjects || []}
              onWorkspaceInputChange={onWorkspaceInputChange}
              onOpenWorkspace={onOpenWorkspace}
            />
          )}
        </div>
      )}
    </aside>
  );
}
