import { escapeHtml } from "../core/format.js";

const TOOL_CAPABILITY_TRACE = {
  write_file: { capabilityName: "文件读写", capabilityId: "tool.file_ops", kind: "tool", agent: "Coder" },
  edit_file: { capabilityName: "文件读写", capabilityId: "tool.file_ops", kind: "tool", agent: "Coder" },
  read_file: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Coder" },
  list_directory: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  search_codebase: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  project_context: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  bash: { capabilityName: "交付复核 Skill", capabilityId: "skill.delivery-review", kind: "skill", agent: "Tester" },
  task_create: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Planner" },
  task_update: { capabilityName: "项目索引", capabilityId: "tool.project_index", kind: "tool", agent: "Lead" },
  add_memory: { capabilityName: "偏好记忆", capabilityId: "tool.memory", kind: "tool", agent: "Lead" },
  recall_memories: { capabilityName: "偏好记忆", capabilityId: "tool.memory", kind: "tool", agent: "Planner" },
};

export function isTemporaryProjectPath(path) {
  const text = String(path || "").toLowerCase();
  return (
    text.includes("/pytest-") ||
    text.includes("\\pytest-") ||
    text.includes("/e2e_workspaces/") ||
    text.includes("\\e2e_workspaces\\") ||
    text.includes("/tmp/") ||
    text.includes("\\tmp\\") ||
    text.includes("/temp/") ||
    text.includes("\\temp\\")
  );
}

export function visibleRecentProjects(state, limit = 6) {
  const projects = state.recentProjects || [];
  return projects.filter((item) => !isTemporaryProjectPath(item.path)).slice(0, limit);
}

export function statusColor(status) {
  const colors = {
    created: "var(--slate)",
    planning: "var(--blue)",
    waiting_approval: "var(--amber)",
    running: "var(--green)",
    validating: "var(--blue)",
    cancelling: "var(--amber)",
    completed: "var(--green)",
    failed: "var(--coral)",
    cancelled: "var(--slate)",
    interrupted: "var(--coral)",
    recovering: "var(--amber)",
  };
  return colors[status] || "var(--slate)";
}

export function getCapabilityOptions(state) {
  const groups = state.capabilityHub?.groups || [];
  return groups
    .flatMap((group) => group.items || [])
    .filter((item) => item.status !== "planned");
}

export function capabilityDisplayName(state, capabilityId) {
  const capability = (state.capabilityHub?.capabilities || getCapabilityOptions(state)).find((item) => item.id === capabilityId);
  return capability?.name || capabilityId;
}

export function capabilityTraceForEvent(state, event) {
  const trace = event.payload?.capability_trace;
  if (trace) {
    return {
      agent: trace.agent || event.agent || "Lead",
      capabilityName: trace.capability_name || trace.capabilityName || capabilityDisplayName(state, trace.capability_id),
      capabilityId: trace.capability_id || trace.capabilityId || "",
      kind: trace.kind || "tool",
      tool: trace.tool || event.payload?.tool || "",
    };
  }

  const tool = event.payload?.tool || String(event.title || "").match(/(?:工具调用|能力调用)：(.+)$/)?.[1];
  if (!tool) return null;
  const inferred = TOOL_CAPABILITY_TRACE[tool] || {
    capabilityName: "通用工具",
    capabilityId: "tool.generic",
    kind: "tool",
    agent: event.agent || "Lead",
  };
  return { ...inferred, tool };
}

export function renderEventCapabilityTrace(state, event) {
  if (event.type !== "tool_call_finished" && event.type !== "capability_used") return "";
  const trace = capabilityTraceForEvent(state, event);
  if (!trace) return "";
  return `
    <div class="event-capability">
      <span>${escapeHtml(trace.agent)}</span>
      <strong>${escapeHtml(trace.capabilityName)}</strong>
      ${trace.tool ? `<em>${escapeHtml(trace.tool)}</em>` : ""}
    </div>
  `;
}

export function inferTaskCapabilities(task) {
  const title = `${task?.title || ""} ${task?.description || ""}`.toLowerCase();
  const owner = String(task?.owner || "").toLowerCase();
  const capabilities = [];

  function add(item) {
    if (item && !capabilities.includes(item)) capabilities.push(item);
  }

  if (owner.includes("planner") || ["需求", "验收", "计划", "拆解", "文档", "接口"].some((keyword) => title.includes(keyword))) {
    add("tool.project_index");
  }
  if (owner.includes("coder") || ["实现", "代码", "界面", "本地存储", "文件", "样式"].some((keyword) => title.includes(keyword))) {
    add("tool.file_ops");
    add("tool.project_index");
  }
  if (["界面", "前端", "ui", "样式", "布局", "交互"].some((keyword) => title.includes(keyword))) {
    add("skill.frontend-polish");
  }
  if (owner.includes("tester") || ["测试", "验证", "质量", "报告", "复核"].some((keyword) => title.includes(keyword))) {
    add("skill.delivery-review");
    add("tool.recovery");
  }
  return capabilities.slice(0, 5);
}

export function eventKind(type) {
  if (type === "tool_call_finished") return "tool";
  if (type === "capability_used") return "tool";
  if (type === "done") return "done";
  if (type === "error") return "error";
  return "message";
}
