import { BookOpen, ChevronLeft, ChevronRight, GraduationCap, Map, MessageSquareText, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Search } from "lucide-react";
import { documentRoute } from "../content/contentLoader.js";

const GROUP_ICONS = {
  chapters: BookOpen,
  maps: Map,
  exercises: GraduationCap,
  interview: MessageSquareText,
};

export default function AppShell({
  documents,
  activeDoc,
  progress,
  stats,
  searchQuery,
  onSearchChange,
  children,
  sidebarExtra,
  aside,
  sidebarCollapsed,
  onToggleSidebar,
  rightRailCollapsed,
  onToggleRightRail,
}) {
  const groups = documents.reduce((acc, doc) => {
    acc[doc.group] = acc[doc.group] || { label: doc.groupLabel, items: [] };
    acc[doc.group].items.push(doc);
    return acc;
  }, {});

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${rightRailCollapsed ? "rail-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="sidebar-inner">
          <div className="sidebar-top">
            <a className="brand" href="#/">
              <span className="brand-mark">n</span>
              {!sidebarCollapsed && (
                <span>
                  <strong>nanoCursor</strong>
                  <small>Learning</small>
                </span>
              )}
            </a>

            {!sidebarCollapsed && (
              <>
                <label className="search-box">
                  <Search size={17} />
                  <input
                    value={searchQuery}
                    onChange={(event) => onSearchChange(event.target.value)}
                    placeholder="搜索章节、源码、概念"
                  />
                </label>

                <div className="progress-card compact">
                  <span>学习进度</span>
                  <strong>{stats.completed}/{stats.total}</strong>
                  <div className="progress-track">
                    <div style={{ width: `${stats.percent}%` }} />
                  </div>
                </div>
              </>
            )}

            {sidebarCollapsed && (
              <button className="sidebar-search-icon" onClick={() => { onToggleSidebar(); setTimeout(() => document.querySelector(".search-box input")?.focus(), 200); }} title="搜索">
                <Search size={20} />
              </button>
            )}
          </div>

          {sidebarCollapsed ? (
            <nav className="doc-nav-collapsed">
              {Object.entries(groups).map(([group, data]) => {
                const Icon = GROUP_ICONS[group] || BookOpen;
                return (
                  <div key={group} className="nav-group-icon" title={data.label}>
                    <Icon size={20} />
                    {data.items.map((doc) => (
                      <a
                        key={doc.id}
                        className={`nav-dot ${activeDoc?.id === doc.id ? "active" : ""} ${progress[doc.id]?.completed ? "done" : ""}`}
                        href={documentRoute(doc)}
                        title={doc.title}
                      />
                    ))}
                  </div>
                );
              })}
            </nav>
          ) : (
            <nav className="doc-nav">
              {Object.entries(groups).map(([group, data]) => {
                const Icon = GROUP_ICONS[group] || BookOpen;
                return (
                  <section key={group} className="nav-group">
                    <h2>
                      <Icon size={16} />
                      {data.label}
                    </h2>
                    {data.items.map((doc) => (
                      <a
                        key={doc.id}
                        className={`nav-item ${activeDoc?.id === doc.id ? "active" : ""}`}
                        href={documentRoute(doc)}
                      >
                        <span className={`read-dot ${progress[doc.id]?.completed ? "done" : ""}`} />
                        <span>{doc.title}</span>
                      </a>
                    ))}
                  </section>
                );
              })}
            </nav>
          )}
          {!sidebarCollapsed && sidebarExtra}
        </div>

        <button className="sidebar-toggle" onClick={onToggleSidebar} title={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}>
          {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </aside>

      <main className="main-pane">
        {rightRailCollapsed && aside && (
          <button className="rail-toggle-floating" onClick={onToggleRightRail} title="展开右侧栏">
            <PanelRightOpen size={18} />
          </button>
        )}
        {children}
      </main>

      <aside className={`right-rail ${rightRailCollapsed ? "hidden" : ""}`}>
        <div className="rail-inner">
          <button className="rail-toggle" onClick={onToggleRightRail} title="收起右侧栏">
            <PanelRightClose size={16} />
          </button>
          {aside}
        </div>
      </aside>
    </div>
  );
}
