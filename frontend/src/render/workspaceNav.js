import { escapeHtml, runTitle, shortPath, statusLabel } from "../core/format.js";

export function renderSidebarContent(state) {
  if (state.leftTab === "project") {
    return { className: "project-overview", html: renderProjectOverview(state) };
  }
  if (state.leftTab === "files") {
    return { className: "file-tree", html: state.files.map(renderFileRow).join("") };
  }
  return { className: "run-list", html: renderRunList(state) };
}

export function renderRecentProjectsHtml(projects) {
  if (!projects.length) {
    return "<div><span>暂无最近项目</span></div>";
  }
  return projects
    .slice(0, 6)
    .map(
      (project) => `
    <button class="project-mini-item" data-action="open-recent" data-path="${escapeHtml(project.path)}">
      <strong class="project-mini-title">${escapeHtml(project.name)}</strong>
      <span class="project-mini-meta">${escapeHtml(project.last_opened_at?.slice(0, 10) || "")}</span>
    </button>
  `,
    )
    .join("");
}

function runGroupLabel(run) {
  const time = String(run.time || "");
  if (time.includes(":")) return "今天";
  if (time.includes("昨天")) return "昨天";
  return "更早";
}

function renderRunList(state) {
  const groups = ["今天", "昨天", "更早"];
  return (
    groups
      .map((group) => {
        const items = state.runs.filter((run) => runGroupLabel(run) === group);
        if (!items.length) return "";
        return `
        <section class="run-group">
          <div class="run-group-title">
            <span>${escapeHtml(group)}</span>
            <strong>${escapeHtml(items.length)}</strong>
          </div>
          ${items.map((run) => renderRunItem(run, state)).join("")}
        </section>
      `;
      })
      .join("") || `<div class="project-empty">暂无会话</div>`
  );
}

function renderProjectOverview(state) {
  const overview = state.projectOverview || {};
  const summary = overview.summary || {};
  const index = overview.project_index || {};
  const recovery = overview.recovery || {};
  const recentRuns = overview.recent_runs || [];
  const recentConversations = overview.recent_conversations || [];
  const skills = overview.skills || [];
  const mcp = overview.mcp || [];
  const workspaceMeta = state.workspaceMeta || {};
  const isDefaultWorkspace =
    workspaceMeta.is_default_workspace ||
    (workspaceMeta.default_workspace && (overview.workspace_dir || state.workspaceDir) === workspaceMeta.default_workspace);
  const statItems = [
    ["会话", summary.conversation_count ?? 0],
    ["Runs", summary.recent_run_count ?? 0],
    ["失败", summary.failed_run_count ?? 0],
    ["Skills", summary.skill_count ?? 0],
    ["MCP", summary.configured_mcp_count ?? 0],
    ["恢复点", summary.recovery_point_count ?? 0],
  ];
  return `
    <div class="project-card">
      <div class="project-path-label">当前项目</div>
      <strong title="${escapeHtml(overview.workspace_dir || state.workspaceDir || "")}">
        ${escapeHtml(shortPath(overview.workspace_dir || state.workspaceDir || "未打开项目目录"))}
      </strong>
      ${
        isDefaultWorkspace
          ? `<div class="workspace-default-note">默认隔离工作区。要修改真实项目，请点击顶部“打开”选择项目目录。</div>`
          : ""
      }
      <button class="button secondary compact-button" data-action="refresh-project-overview" type="button">同步</button>
    </div>

    <div class="project-actions">
      <button class="button primary compact-button" data-action="new-session" type="button">+ 新建会话</button>
      <button class="button secondary compact-button" data-action="refresh-project-overview" type="button">刷新索引</button>
      <button class="button secondary compact-button" data-action="goto-capabilities" type="button">配置 MCP</button>
      <button class="button secondary compact-button" data-action="goto-capabilities" type="button">导入 Skill</button>
    </div>

    <div class="project-stat-grid">
      ${statItems
        .map(
          ([label, value]) => `
            <div class="project-stat">
              <strong>${escapeHtml(value)}</strong>
              <span>${escapeHtml(label)}</span>
            </div>
          `,
        )
        .join("")}
    </div>

    <section class="project-section">
      <div class="project-section-title">
        <strong>项目索引</strong>
        <span>${escapeHtml(index.total_files || 0)} 文件 · ${escapeHtml(index.total_loc || 0)} LOC</span>
      </div>
      <div class="project-chip-row">
        ${(index.entry_points || []).slice(0, 4).map((item) => `<span>${escapeHtml(item)}</span>`).join("") || `<span>等待索引</span>`}
      </div>
      <div class="project-mini-list">
        ${(index.recently_modified || [])
          .slice(0, 4)
          .map((item) => `<div><span>${escapeHtml(item.path || item)}</span></div>`)
          .join("") || `<div><span>暂无最近修改</span></div>`}
      </div>
    </section>

    <section class="project-section">
      <div class="project-section-title">
        <strong>最近会话</strong>
        <span>${escapeHtml(recentConversations.length)} 条</span>
      </div>
      <div class="project-mini-list">
        ${recentConversations
          .slice(0, 4)
          .map(
            (item) => `
              <button class="project-mini-item" data-action="select-run" data-run-id="${escapeHtml(item.conversation_id)}">
                <strong class="project-mini-title">${escapeHtml(runTitle(item.prompt, item.conversation_id))}</strong>
                <span class="project-mini-meta">${escapeHtml(statusLabel(item.status))}</span>
              </button>
            `,
          )
          .join("") || `<div class="project-empty">暂无会话</div>`}
      </div>
    </section>

    <section class="project-section">
      <div class="project-section-title">
        <strong>最近运行</strong>
        <span>${escapeHtml(recentRuns.length)} 条</span>
      </div>
      <div class="project-mini-list">
        ${recentRuns
          .slice(0, 4)
          .map(
            (item) => `
              <button class="project-mini-item" data-action="select-run" data-run-id="${escapeHtml(item.thread_id)}">
                <strong class="project-mini-title">${escapeHtml(runTitle(item.prompt, item.thread_id))}</strong>
                <span class="project-mini-meta">${escapeHtml(statusLabel(item.status))}</span>
              </button>
            `,
          )
          .join("") || `<div class="project-empty">暂无运行</div>`}
      </div>
    </section>

    <section class="project-section">
      <div class="project-section-title">
        <strong>能力接入</strong>
        <span>${escapeHtml(summary.custom_skill_count || 0)} 自定义 Skill</span>
      </div>
      <div class="project-chip-row">
        ${skills
          .slice(0, 4)
          .map((item) => `<span>${escapeHtml(item.name || item.id)}</span>`)
          .join("") || `<span>暂无 Skill</span>`}
      </div>
      <div class="project-chip-row muted">
        ${mcp
          .slice(0, 3)
          .map((item) => `<span>${escapeHtml(item.name || item.id)} · ${escapeHtml(statusLabel(item.status))}</span>`)
          .join("") || `<span>暂无 MCP 配置</span>`}
      </div>
    </section>

    <section class="project-section">
      <div class="project-section-title">
        <strong>恢复状态</strong>
        <span>${escapeHtml(statusLabel(recovery.status))}</span>
      </div>
      <div class="project-mini-list">
        ${(recovery.actions || [])
          .slice(0, 3)
          .map((item) => `<div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.priority)}</span></div>`)
          .join("") || `<div><span>暂无恢复建议</span></div>`}
      </div>
    </section>

    ${renderProjectHealth(overview.health)}

    <section class="project-section">
      <div class="project-section-title">
        <strong>最近项目</strong>
      </div>
      <div class="project-mini-list" id="recent-projects-list">
        <div><span>加载中...</span></div>
      </div>
    </section>
  `;
}

function renderProjectHealth(health) {
  if (!health) return "";
  const checks = [
    ["目录存在", health.exists],
    ["可写", health.writable],
    ["Git 仓库", health.is_git_repo],
    ["索引完成", health.index_status === "indexed"],
  ];
  return `
    <section class="project-section">
      <div class="project-section-title">
        <strong>工作区健康</strong>
        <span>${health.run_count ?? 0} runs · ${health.backup_count ?? 0} 备份</span>
      </div>
      <div class="health-checks">
        ${checks
          .map(
            ([label, ok]) => `
          <div class="health-check ${ok ? "ok" : "warn"}">
            <span>${ok ? "✓" : "✗"}</span>
            ${escapeHtml(label)}
          </div>
        `,
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderRunItem(run, state) {
  const active =
    run.kind === "conversation"
      ? run.conversationId === state.currentConversationId
      : run.id === state.currentThreadId
        ? "active"
        : "";
  const details = [
    run.kind === "conversation" ? "会话" : "",
    statusLabel(run.status),
    run.time,
    run.localOnly ? "草稿" : "",
    run.agentCount ? `${run.agentCount} Agent` : "",
    run.eventCount ? `${run.eventCount} 事件` : "",
    run.changedFilesCount ? `${run.changedFilesCount} 文件` : "",
  ].filter(Boolean);
  return `
    <button class="run-item ${active}" data-action="select-run" data-run-id="${escapeHtml(run.id)}">
      <span class="run-title">${escapeHtml(run.title || run.id)}</span>
      <span class="run-meta">
        ${details.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
      </span>
    </button>
  `;
}

function renderFileRow(file) {
  const icon = file.type.slice(0, 2).toUpperCase();
  return `
    <div class="file-row ${file.active ? "active" : ""}">
      <span class="file-icon">${escapeHtml(icon)}</span>
      <span title="${escapeHtml(file.path)}">${escapeHtml(file.path)}</span>
    </div>
  `;
}
