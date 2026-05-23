export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function nowTime() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

export function formatTime(timestamp) {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

export function runTitle(prompt, fallback = "历史运行") {
  const text = String(prompt || "").trim();
  return text ? text.slice(0, 24) : fallback;
}

export function shortPath(path) {
  const parts = String(path || "").split(/[\\/]+/).filter(Boolean);
  if (parts.length <= 3) return path;
  return `.../${parts.slice(-3).join("/")}`;
}

export function shortId(value, fallback = "未创建") {
  const text = String(value || "").trim();
  if (!text) return fallback;
  if (text.length <= 14) return text;
  return `${text.slice(0, 8)}...${text.slice(-4)}`;
}

export function statusLabel(status) {
  const labels = {
    idle: "空闲",
    draft: "草稿",
    created: "已创建",
    planning: "规划中",
    waiting_approval: "等待审批",
    running: "运行中",
    validating: "验证中",
    cancelling: "取消中",
    completed: "完成",
    failed: "失败",
    cancelled: "取消",
    interrupted: "已中断",
    recovering: "恢复中",
    pending: "待处理",
    in_progress: "进行中",
    skipped: "跳过",
    review: "复核",
    error: "异常",
    safe: "安全",
    attention: "需关注",
    unprotected: "未保护",
    planned: "待接入",
    configured: "已配置",
    ready: "就绪",
    missing: "缺失",
    unknown: "未知",
    replaying: "回放中",
    replay_paused: "已暂停",
    active: "活跃",
    working: "工作中",
    waiting_input: "等输入",
    archived: "已归档",
    expired: "已过期",
  };
  return labels[status] || status || "未知";
}

export function replayStatusLabel(status) {
  const labels = {
    idle: "未载入",
    ready: "已载入",
    playing: "播放中",
    paused: "已暂停",
    finished: "已完成",
  };
  return labels[status] || status || "未知";
}

export function approvalDecisionLabel(decision) {
  const labels = {
    approved: "已批准",
    revise: "需修改",
    rejected: "已拒绝",
  };
  return labels[decision] || decision || "待审批";
}

export function capabilityKindLabel(kind) {
  const labels = {
    tool: "内置工具",
    mcp: "MCP",
    skill: "Skill",
  };
  return labels[kind] || kind || "能力";
}

export function capabilityStatusLabel(status) {
  const labels = {
    ready: "可用",
    configured: "已配置",
    planned: "待接入",
  };
  return labels[status] || status || "未知";
}
