const configuredApiBase = localStorage.getItem("agenthub_api_base");
const LAYOUT_STORAGE_KEY = "agenthub_layout";
const RECOMMENDATION_MUTED_KEY = "agenthub_recommendation_muted";
const DEFAULT_LAYOUT = {
  sidebarCollapsed: false,
  rightCollapsed: false,
  bottomCollapsed: true,
};
const API_CANDIDATES = configuredApiBase
  ? [configuredApiBase]
  : ["http://127.0.0.1:8100", "http://127.0.0.1:8101", "http://127.0.0.1:8102"];
let activeApiBase = API_CANDIDATES[0];

const demoState = {
  status: "idle",
  activeTab: "diff",
  leftTab: "runs",
  rightTab: "tasks",
  currentThreadId: "demo-run",
  currentConversationId: "",
  workspaceDir: localStorage.getItem("agenthub_workspace_dir") || "",
  workspaceInput: localStorage.getItem("agenthub_workspace_dir") || "",
  projectOverview: {
    workspace_dir: localStorage.getItem("agenthub_workspace_dir") || "",
    summary: {
      conversation_count: 0,
      recent_run_count: 0,
      failed_run_count: 0,
      skill_count: 2,
      custom_skill_count: 0,
      configured_mcp_count: 0,
      recovery_point_count: 1,
      risk_count: 0,
      source_count: 0,
      test_count: 0,
      route_count: 0,
    },
    project_index: {
      status: "demo",
      entry_points: ["api_server.py", "frontend/src/main.js"],
      total_files: 0,
      source_count: 0,
      test_count: 0,
      total_loc: 0,
      recently_modified: [],
      routes: [],
      route_count: 0,
    },
    recent_conversations: [],
    recent_runs: [],
    skills: [],
    mcp: [],
    recovery: {
      status: "safe",
      summary: {},
      recent_points: [],
      risks: [],
      actions: [],
    },
  },
  layout: loadLayoutPreference(),
  prompt:
    "帮我做一个 Todo Web App，要求支持新增、完成、删除、搜索和本地存储，并给出测试说明。",
  replay: {
    events: [],
    index: 0,
    speed: 1,
    status: "idle",
    prompt: "",
    startedAt: "",
  },
  approval: {
    status: "idle",
    planId: "",
    title: "",
    content: "",
    riskLevel: "",
    tasks: [],
    decision: "",
    comment: "",
  },
  runs: [
    {
      id: "demo-run",
      title: "Todo Web App 交付",
      status: "completed",
      time: "14:20",
    },
    {
      id: "python-feature",
      title: "Python 工具补测试",
      status: "review",
      time: "昨天",
    },
  ],
  conversations: [],
  messages: [
    {
      role: "user",
      author: "用户",
      time: "14:20",
      content:
        "帮我做一个 Todo Web App，要求支持新增、完成、删除、搜索和本地存储，并给出测试说明。",
    },
    {
      role: "assistant",
      author: "Lead Agent",
      time: "14:20",
      content:
        "我会按 nanoCursor 交付流程执行：先拆解需求，再由 Coder 生成前端实现，Tester 验证交互和本地存储，最后给出交付报告。",
    },
    {
      role: "assistant",
      author: "Reviewer Agent",
      time: "14:24",
      content:
        "实现已覆盖核心验收标准。建议下一轮补充键盘快捷操作和空状态文案，当前版本可以作为比赛演示用例。",
    },
  ],
  tasks: [
    {
      id: "task-001",
      title: "需求整理与验收标准",
      description: "明确新增、完成、删除、搜索、本地存储和测试说明。",
      status: "completed",
      owner: "Planner",
      capabilities: ["tool.project_index"],
    },
    {
      id: "task-002",
      title: "实现 Todo 交互界面",
      description: "构建列表、输入框、过滤搜索和状态切换。",
      status: "completed",
      owner: "Coder",
      capabilities: ["tool.file_ops", "tool.project_index", "skill.frontend-polish"],
    },
    {
      id: "task-003",
      title: "接入本地存储",
      description: "使用 localStorage 保存任务数据和完成状态。",
      status: "completed",
      owner: "Coder",
      capabilities: ["tool.file_ops", "tool.project_index"],
    },
    {
      id: "task-004",
      title: "验证和交付报告",
      description: "检查核心流程并生成面向用户的交付摘要。",
      status: "in_progress",
      owner: "Tester",
      capabilities: ["skill.delivery-review", "tool.recovery"],
    },
  ],
  team: [
    {
      name: "Lead",
      role: "总控协调",
      status: "idle",
      initials: "L",
      tone: "lead",
      goal: "协调需求、任务、验证和交付报告。",
      tools: ["plan", "delegate", "report"],
      lastAction: "等待新的交付任务。",
      artifacts: ["report", "score"],
    },
    {
      name: "Planner",
      role: "需求拆解",
      status: "idle",
      initials: "P",
      tone: "planner",
      goal: "把用户需求拆成任务、依赖和验收点。",
      tools: ["task_create", "task_update"],
      lastAction: "维护任务板和需求覆盖。",
      artifacts: ["tasks", "requirements"],
    },
    {
      name: "Coder",
      role: "代码实现",
      status: "idle",
      initials: "C",
      tone: "coder",
      goal: "完成代码改动并保持 Diff 可审查。",
      tools: ["write_file", "edit_file", "bash"],
      lastAction: "准备处理文件变更。",
      artifacts: ["changed_files", "diff_patch"],
    },
    {
      name: "Tester",
      role: "验证交付",
      status: "running",
      initials: "T",
      tone: "tester",
      goal: "验证交付结果并暴露风险。",
      tools: ["bash", "manual_check"],
      lastAction: "检查核心验收路径。",
      artifacts: ["tests", "quality"],
    },
  ],
  events: [
    {
      type: "run_started",
      title: "任务已启动",
      content: "创建 nanoCursor 交付会话，并初始化任务板。",
      time: "14:20",
    },
    {
      type: "plan_created",
      title: "Planner 生成方案",
      content: "拆解为需求整理、界面实现、本地存储、验证报告四个任务。",
      time: "14:21",
    },
    {
      type: "tool_call_finished",
      title: "工具调用：write_file",
      content: "写入 src/App.tsx 和 src/styles.css。",
      time: "14:22",
    },
    {
      type: "tool_call_finished",
      title: "工具调用：bash",
      content: "执行前端构建检查，返回成功。",
      time: "14:23",
    },
    {
      type: "done",
      title: "交付报告已生成",
      content: "变更文件、验证结果和下一步建议已归档。",
      time: "14:24",
    },
  ],
  files: [
    { path: "demo-todo/index.html", type: "html", active: false },
    { path: "demo-todo/src/App.tsx", type: "tsx", active: true },
    { path: "demo-todo/src/styles.css", type: "css", active: false },
    { path: "demo-todo/package.json", type: "json", active: false },
    { path: "workspace/.nanocursor/runs/demo-run/report.md", type: "md", active: false },
  ],
  metrics: {
    tasks: 4,
    files: 4,
    toolCalls: 9,
    tokens: "12.8k",
    tests: "3/3",
  },
  capabilityHub: {
    summary: {
      total: 9,
      ready: 6,
      configured: 0,
      planned: 3,
    },
    groups: [
      {
        id: "tool",
        label: "内置工具",
        items: [
          {
            id: "tool.file_ops",
            name: "文件读写",
            kind: "tool",
            status: "ready",
            description: "读取、编辑、写入项目文件，并把变更沉淀到 Diff 与交付物。",
            tags: ["write_file", "edit_file", "diff"],
            agents: ["Coder", "Reviewer"],
          },
          {
            id: "tool.project_index",
            name: "项目索引",
            kind: "tool",
            status: "ready",
            description: "按符号、依赖、入口点理解代码库，减少盲目搜索。",
            tags: ["search_codebase", "project_context"],
            agents: ["Planner", "Coder"],
          },
          {
            id: "tool.memory",
            name: "偏好记忆",
            kind: "tool",
            status: "ready",
            description: "记录用户风格、技术栈和历史反馈，让 nanoCursor 越用越懂项目。",
            tags: ["add_memory", "recall_memories"],
            agents: ["Lead", "Planner"],
          },
          {
            id: "tool.recovery",
            name: "安全恢复",
            kind: "tool",
            status: "ready",
            description: "汇总备份、快照、风险，并支持受控文件回滚。",
            tags: ["snapshot", "rollback", "risk"],
            agents: ["Lead", "Tester"],
          },
        ],
      },
      {
        id: "mcp",
        label: "MCP 连接器",
        items: [
          {
            id: "mcp.github",
            name: "GitHub MCP",
            kind: "mcp",
            status: "planned",
            description: "接入 Issue、PR、代码审查和 CI 状态，形成真实研发协作闭环。",
            tags: ["issues", "pull_requests", "ci"],
            agents: ["Lead", "Reviewer"],
          },
          {
            id: "mcp.figma",
            name: "Figma MCP",
            kind: "mcp",
            status: "planned",
            description: "读取设计稿和组件规范，辅助 Designer / Coder 保持 UI 一致性。",
            tags: ["design", "components", "handoff"],
            agents: ["Designer", "Coder"],
          },
          {
            id: "mcp.docs",
            name: "文档知识库 MCP",
            kind: "mcp",
            status: "planned",
            description: "连接项目文档、接口说明和规范库，支持需求追踪与答疑。",
            tags: ["docs", "knowledge", "rag"],
            agents: ["Planner", "Tester"],
          },
        ],
      },
      {
        id: "skill",
        label: "Skills",
        items: [
          {
            id: "skill.frontend-polish",
            name: "前端体验打磨 Skill",
            kind: "skill",
            status: "ready",
            description: "沉淀浅色系、低拥挤、可折叠、按钮连续性的 UI 偏好。",
            tags: ["ui", "layout", "interaction"],
            agents: ["Designer", "Coder"],
            use_cases: ["浅色系工作台美化", "拥挤界面降噪", "折叠与响应式交互"],
            inputs: ["用户 UI 偏好", "当前页面结构", "截图反馈"],
            outputs: ["视觉改进建议", "前端样式补丁", "交互验收清单"],
            risks: ["可能影响已有布局密度，需要保留可扫描性。"],
          },
          {
            id: "skill.delivery-review",
            name: "交付复核 Skill",
            kind: "skill",
            status: "ready",
            description: "从需求覆盖、质量门禁、Diff 风险和恢复点复核一次交付。",
            tags: ["review", "quality", "traceability"],
            agents: ["Reviewer", "Tester"],
            use_cases: ["交付前验收", "风险复盘", "比赛演示质量检查"],
            inputs: ["任务清单", "Diff 摘要", "测试结果", "交付报告"],
            outputs: ["覆盖率判断", "风险列表", "下一步修复建议"],
            risks: ["依赖输入证据完整度，缺少测试结果时只能给出部分结论。"],
          },
        ],
      },
    ],
  },
  capabilityRecommendation: {
    agents: ["Lead", "Planner", "Coder", "Tester"],
    capabilities: [],
    reasons: ["默认按完整软件交付流程推荐：先理解项目，再实现变更，最后复核质量。"],
    summary: {
      agent_count: 4,
      capability_count: 0,
      ready_count: 0,
      planned_count: 0,
    },
  },
  capabilityRecommendationDismissed: false,
  capabilityRecommendationMuted: sessionStorage.getItem(RECOMMENDATION_MUTED_KEY) === "1",
  showCompletedTasks: false,
  runBlueprint: {
    status: "idle",
    prompt: "",
    title: "",
    agents: [],
    capabilities: [],
    stages: [],
    risks: [],
    reasons: [],
    summary: {},
  },
  benchmarks: [
    {
      id: "todo-web-app",
      title: "Todo Web App",
      description: "交付支持新增、完成、删除、搜索和本地存储的前端小应用。",
      difficulty: "easy",
      category: "frontend",
      acceptance_criteria: ["create", "complete", "delete", "search", "localStorage"],
    },
    {
      id: "python-utils",
      title: "Python 工具函数补测试",
      description: "新增 slugify 工具函数并补充基础单元测试。",
      difficulty: "medium",
      category: "backend",
      acceptance_criteria: ["slugify spaces", "strip punctuation", "tests pass"],
    },
    {
      id: "bugfix-cart",
      title: "修复购物车数量 bug",
      description: "修复负数数量导致总价异常的问题，并补充回归测试。",
      difficulty: "medium",
      category: "bugfix",
      acceptance_criteria: ["reject negative quantity", "preserve total calculation", "regression test"],
    },
  ],
  previewUrl: "localhost:5173/demo-todo",
  selectedDiffFile: "",
  diffFiles: [],
  diff: `diff --git a/demo-todo/src/App.tsx b/demo-todo/src/App.tsx
new file mode 100644
--- /dev/null
+++ b/demo-todo/src/App.tsx
@@
+function TodoApp() {
+  const [items, setItems] = useLocalStorage("todos", []);
+  const [query, setQuery] = useState("");
+
+  const visibleItems = items.filter((item) =>
+    item.title.toLowerCase().includes(query.toLowerCase())
+  );
+
+  return (
+    <main className="todo-shell">
+      <TodoComposer onCreate={createItem} />
+      <TodoSearch value={query} onChange={setQuery} />
+      <TodoList items={visibleItems} onToggle={toggleItem} onDelete={deleteItem} />
+    </main>
+  );
+}`,
  report: {
    summary:
      "本次交付完成了一个支持新增、完成、删除、搜索和本地存储的 Todo Web App，并补充了手动测试说明。",
    markdown: "",
    requirements: [
      "支持创建任务并即时展示。",
      "支持完成状态切换和删除。",
      "支持按关键字搜索任务。",
      "刷新页面后保留任务数据。",
    ],
    changedFiles: [
      "demo-todo/src/App.tsx",
      "demo-todo/src/styles.css",
      "demo-todo/index.html",
      "demo-todo/package.json",
    ],
    risks: ["当前演示版本未接入自动化端到端测试。", "部署流程仍为本地预览，正式发布需要补充构建配置。"],
    traceability: {
      source: "demo",
      coverageRate: 1,
      totalCount: 4,
      coveredCount: 4,
      partialCount: 0,
      missingCount: 0,
      requirements: [
        {
          id: "REQ-001",
          title: "创建 Todo",
          status: "covered",
          tasks: ["task-002"],
          files: ["demo-todo/src/App.tsx"],
          tests: ["create"],
        },
        {
          id: "REQ-002",
          title: "完成和删除 Todo",
          status: "covered",
          tasks: ["task-002"],
          files: ["demo-todo/src/App.tsx"],
          tests: ["complete", "delete"],
        },
        {
          id: "REQ-003",
          title: "搜索 Todo",
          status: "covered",
          tasks: ["task-002"],
          files: ["demo-todo/src/App.tsx"],
          tests: ["search"],
        },
        {
          id: "REQ-004",
          title: "本地持久化",
          status: "covered",
          tasks: ["task-003"],
          files: ["demo-todo/src/App.tsx"],
          tests: ["localStorage"],
        },
      ],
    },
  },
  artifactCenter: {
    status: "ready",
    summary: {
      artifact_count: 9,
      ready_count: 8,
      warning_count: 1,
      missing_count: 0,
      score: 92,
      coverage_rate: 1,
    },
    artifacts: [
      {
        id: "requirements",
        kind: "requirements",
        label: "需求摘要",
        status: "ready",
        summary: "4 / 4 个需求已覆盖",
        count: 4,
      },
      {
        id: "tasks",
        kind: "tasks",
        label: "任务清单",
        status: "warning",
        summary: "3 / 4 个任务已完成",
        count: 4,
      },
      {
        id: "changed_files",
        kind: "files",
        label: "变更文件",
        status: "ready",
        summary: "4 个文件发生变化",
        count: 4,
      },
      {
        id: "diff_patch",
        kind: "diff",
        label: "Diff Patch",
        status: "ready",
        summary: "Diff 来源：demo",
      },
      {
        id: "report",
        kind: "report",
        label: "交付报告",
        status: "ready",
        summary: "已生成面向评审的交付摘要",
      },
    ],
  },
  memoryProfile: {
    total_memories: 3,
    preference_count: 3,
    high_importance_count: 2,
    prompt_context:
      "代码风格:\n- 偏好小步提交、清晰命名和必要注释。\nUI 风格:\n- 偏好克制、专业、信息密度高的工作台界面。",
    buckets: [
      {
        id: "code_style",
        label: "代码风格",
        description: "命名、注释、类型、格式化和代码组织习惯。",
        confidence: "high",
        memories: [{ id: "demo-code", content: "偏好小步提交、清晰命名和必要注释。", importance: 8, tags: [] }],
      },
      {
        id: "ui_style",
        label: "UI 风格",
        description: "界面审美、布局密度、颜色、交互和组件偏好。",
        confidence: "high",
        memories: [{ id: "demo-ui", content: "偏好克制、专业、信息密度高的工作台界面。", importance: 8, tags: [] }],
      },
      {
        id: "testing",
        label: "测试偏好",
        description: "单元测试、端到端测试、验证策略和质量门禁习惯。",
        confidence: "medium",
        memories: [{ id: "demo-test", content: "后端功能需要补充可重复运行的 pytest。", importance: 6, tags: [] }],
      },
    ],
  },
  recoveryCenter: {
    status: "safe",
    summary: {
      snapshot_count: 1,
      backup_count: 3,
      risk_count: 0,
      high_risk_count: 0,
      has_recovery_points: true,
    },
    recovery_points: [
      {
        id: "demo-snapshot",
        kind: "snapshot",
        label: "执行前快照",
        status: "available",
        reason: "before_demo_run",
        detail: "捕获 Demo Run 前的工作区状态。",
      },
      {
        id: "demo-todo_app.js.bak",
        kind: "backup",
        label: "demo-todo_app.js.bak",
        status: "available",
        target_path: "demo-todo/app.js",
        size: 2048,
        detail: "文件备份可用于指定路径回滚。",
      },
    ],
    risks: [],
  },
};

const state = structuredClone(demoState);
let eventSource = null;
let replayTimer = null;
let seenEventIds = new Set();
let recommendationTimer = null;
let recommendationRenderDeferred = false;

function loadLayoutPreference() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_STORAGE_KEY) || "{}");
    return {
      ...DEFAULT_LAYOUT,
      sidebarCollapsed:
        typeof saved.sidebarCollapsed === "boolean" ? saved.sidebarCollapsed : DEFAULT_LAYOUT.sidebarCollapsed,
      rightCollapsed:
        typeof saved.rightCollapsed === "boolean" ? saved.rightCollapsed : DEFAULT_LAYOUT.rightCollapsed,
      bottomCollapsed:
        typeof saved.bottomCollapsed === "boolean" ? saved.bottomCollapsed : DEFAULT_LAYOUT.bottomCollapsed,
    };
  } catch {
    return { ...DEFAULT_LAYOUT };
  }
}

function saveLayoutPreference() {
  try {
    localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({ ...DEFAULT_LAYOUT, ...state.layout }));
  } catch {
    // Ignore storage failures; the layout should still work for the current session.
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function nowTime() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

function formatTime(timestamp) {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

function runTitle(prompt, fallback = "历史运行") {
  const text = String(prompt || "").trim();
  return text ? text.slice(0, 24) : fallback;
}

function shortPath(path) {
  const parts = String(path || "").split(/[\\/]+/).filter(Boolean);
  if (parts.length <= 3) return path;
  return `…/${parts.slice(-3).join("/")}`;
}

function statusLabel(status) {
  const labels = {
    idle: "空闲",
    draft: "草稿",
    running: "运行中",
    completed: "完成",
    failed: "失败",
    cancelled: "取消",
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
  };
  return labels[status] || status || "未知";
}

function replayStatusLabel(status) {
  const labels = {
    idle: "未载入",
    ready: "已载入",
    playing: "播放中",
    paused: "已暂停",
    finished: "已完成",
  };
  return labels[status] || status || "未知";
}

function approvalDecisionLabel(decision) {
  const labels = {
    approved: "已批准",
    revise: "需修改",
    rejected: "已拒绝",
  };
  return labels[decision] || decision || "待审批";
}

function capabilityKindLabel(kind) {
  const labels = {
    tool: "内置工具",
    mcp: "MCP",
    skill: "Skill",
  };
  return labels[kind] || kind || "能力";
}

function capabilityStatusLabel(status) {
  const labels = {
    ready: "可用",
    configured: "已配置",
    planned: "待接入",
  };
  return labels[status] || status || "未知";
}

function getCapabilityOptions() {
  const groups = state.capabilityHub?.groups || [];
  return groups
    .flatMap((group) => group.items || [])
    .filter((item) => item.status !== "planned");
}

function capabilityDisplayName(capabilityId) {
  const capability = (state.capabilityHub?.capabilities || getCapabilityOptions()).find((item) => item.id === capabilityId);
  return capability?.name || capabilityId;
}

function capabilityTraceForEvent(event) {
  const trace = event.payload?.capability_trace;
  if (trace) {
    return {
      agent: trace.agent || event.agent || "Lead",
      capabilityName: trace.capability_name || trace.capabilityName || capabilityDisplayName(trace.capability_id),
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

function renderEventCapabilityTrace(event) {
  if (event.type !== "tool_call_finished" && event.type !== "capability_used") return "";
  const trace = capabilityTraceForEvent(event);
  if (!trace) return "";
  return `
    <div class="event-capability">
      <span>${escapeHtml(trace.agent)}</span>
      <strong>${escapeHtml(trace.capabilityName)}</strong>
      ${trace.tool ? `<em>${escapeHtml(trace.tool)}</em>` : ""}
    </div>
  `;
}

function inferTaskCapabilities(task) {
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

function eventKind(type) {
  if (type === "tool_call_finished") return "tool";
  if (type === "capability_used") return "tool";
  if (type === "done") return "done";
  if (type === "error") return "error";
  return "message";
}

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

function render() {
  const focusedField = captureFocusedField();
  document.querySelector("#app").innerHTML = `
    <div class="app-shell">
      ${renderTopbar()}
      <main class="${layoutClass()}">
        ${renderSidebar()}
        ${renderChat()}
        ${renderRightPanel()}
        ${renderBottomPanel()}
      </main>
    </div>
  `;
  bindEvents();
  restoreFocusedField(focusedField);
  scrollToLatestMessage();
}

function captureFocusedField() {
  const active = document.activeElement;
  if (!active || !["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)) return null;
  return {
    id: active.id,
    value: active.value,
    selectionStart: active.selectionStart,
    selectionEnd: active.selectionEnd,
  };
}

function restoreFocusedField(snapshot) {
  if (!snapshot?.id) return;
  const field = document.querySelector(`#${CSS.escape(snapshot.id)}`);
  if (!field) return;
  field.focus({ preventScroll: true });
  if (typeof snapshot.value === "string" && field.value !== snapshot.value) {
    field.value = snapshot.value;
  }
  if (typeof field.setSelectionRange === "function" && snapshot.selectionStart !== null) {
    field.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd ?? snapshot.selectionStart);
  }
}

function layoutClass() {
  const classes = ["workspace"];
  if (state.layout?.sidebarCollapsed) classes.push("sidebar-collapsed");
  if (state.layout?.rightCollapsed) classes.push("right-collapsed");
  if (state.layout?.bottomCollapsed) classes.push("bottom-collapsed");
  return classes.join(" ");
}

function renderTopbar() {
  const dotClass =
    state.status === "running" || state.status === "replaying"
      ? "running"
      : state.status === "failed"
        ? "error"
        : "";

  return `
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">NC</div>
        <div>nanoCursor</div>
      </div>
      <div class="topbar-meta">
        <span class="pill"><span class="status-dot ${dotClass}"></span><strong>${statusLabel(state.status)}</strong></span>
        <span class="pill">API&nbsp;<strong>${escapeHtml(activeApiBase)}</strong></span>
        <span class="pill">Conv&nbsp;<strong>${escapeHtml(state.currentConversationId || "未创建")}</strong></span>
        <span class="pill">Thread&nbsp;<strong>${escapeHtml(state.currentThreadId)}</strong></span>
        <form class="workspace-picker" id="workspace-form">
          <input id="workspace-input" value="${escapeHtml(state.workspaceInput || state.workspaceDir || "")}" placeholder="打开项目目录绝对路径" />
          <button class="button secondary compact-button" type="submit">打开</button>
        </form>
      </div>
      <div class="topbar-actions">
        <button class="button secondary" data-action="new-session">新会话</button>
        <button class="button" data-action="demo-run">演示运行</button>
        <button class="button secondary" data-action="sync-data">同步</button>
        <button class="button secondary" data-action="reset-demo">重置</button>
        <button class="button secondary" data-action="copy-report">复制报告</button>
      </div>
    </header>
  `;
}

function renderSidebar() {
  const tabs = [
    ["project", "项目", state.projectOverview?.summary?.recent_run_count ?? 0],
    ["runs", "会话", state.runs.length],
    ["files", "文件", state.files.length],
  ];

  if (state.layout?.sidebarCollapsed) {
    return `
      <aside class="sidebar collapsed-rail">
        <section class="panel rail-panel">
          <button class="rail-toggle" data-action="toggle-sidebar" title="展开左侧栏">›</button>
          ${tabs
            .map(
              ([id, label, count]) => `
                <button class="rail-nav-button ${state.leftTab === id ? "active" : ""}" data-action="side-nav" data-side="left" data-tab="${id}" title="${label}">
                  <strong>${escapeHtml(count)}</strong>
                  <span>${escapeHtml(label)}</span>
                </button>
              `,
            )
            .join("")}
        </section>
      </aside>
    `;
  }

  const activeLabel = state.leftTab === "files" ? "文件" : state.leftTab === "project" ? "项目" : "会话";
  const activeCount =
    state.leftTab === "files"
      ? state.files.length
      : state.leftTab === "project"
        ? state.projectOverview?.summary?.recent_run_count ?? 0
        : state.runs.length;
  const activeUnit = state.leftTab === "files" ? "个" : state.leftTab === "project" ? "项" : "条";

  return `
    <aside class="sidebar">
      <section class="panel sidebar-section">
        <div class="panel-header">
          <h2 class="panel-title">${activeLabel}</h2>
          <div class="panel-actions">
            <span class="panel-subtitle">${escapeHtml(activeCount)} ${activeUnit}</span>
            ${state.leftTab === "runs" ? `<button class="icon-button" data-action="new-session" title="新建会话">+</button>` : ""}
            <button class="icon-button" data-action="toggle-sidebar" title="收起左侧栏">‹</button>
          </div>
        </div>
        <div class="side-tabs">
          ${tabs
            .map(
              ([id, label]) =>
                `<button class="tab-button ${state.leftTab === id ? "active" : ""}" data-action="left-tab" data-tab="${id}">${label}</button>`,
            )
            .join("")}
        </div>
        <div class="content-scroll ${state.leftTab === "files" ? "file-tree" : state.leftTab === "project" ? "project-overview" : "run-list"}">
          ${state.leftTab === "project" ? renderProjectOverview() : ""}
          ${state.leftTab === "files" ? state.files.map(renderFileRow).join("") : ""}
          ${state.leftTab === "runs" ? state.runs.map(renderRunItem).join("") : ""}
        </div>
      </section>
    </aside>
  `;
}

function renderProjectOverview() {
  const overview = state.projectOverview || {};
  const summary = overview.summary || {};
  const index = overview.project_index || {};
  const recovery = overview.recovery || {};
  const recentRuns = overview.recent_runs || [];
  const recentConversations = overview.recent_conversations || [];
  const skills = overview.skills || [];
  const mcp = overview.mcp || [];
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
      <button class="button secondary compact-button" data-action="refresh-project-overview" type="button">同步</button>
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
  `;
}

function renderRunItem(run) {
  const active = run.kind === "conversation"
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

function renderChat() {
  return `
    <section class="panel chat-panel">
      <div class="panel-header">
        <h2 class="panel-title">IM 协作区</h2>
        <span class="panel-subtitle">Lead / Planner / Coder / Tester</span>
      </div>
      <div class="chat-body">
        <div class="message-list" id="message-list">
          ${state.messages.map(renderMessage).join("")}
        </div>
        ${renderApprovalPanel()}
        ${renderCapabilityRecommendation()}
        ${renderRunBlueprint()}
        <form class="prompt-box" id="prompt-form">
          <textarea class="prompt-input" id="prompt-input" rows="2" placeholder="输入需求，启动一次 nanoCursor 交付流程">${escapeHtml(state.prompt)}</textarea>
          <button class="button" type="submit" ${state.status === "running" ? "disabled" : ""}>生成蓝图</button>
        </form>
      </div>
    </section>
  `;
}

function renderRunBlueprint() {
  const blueprint = state.runBlueprint || {};
  if (!blueprint.status || blueprint.status === "idle") return "";
  const loading = blueprint.status === "loading";
  const stages = blueprint.stages || [];
  const risks = blueprint.risks || [];
  const capabilities = blueprint.capabilities || [];

  return `
    <section class="blueprint-panel ${escapeHtml(blueprint.status)}">
      <div class="blueprint-head">
        <div>
          <span>Run Blueprint</span>
          <h3>${escapeHtml(loading ? "正在生成执行蓝图" : blueprint.title || "nanoCursor 执行蓝图")}</h3>
        </div>
        <div class="blueprint-head-actions">
          ${
            loading
              ? ""
              : `
                <button class="button compact-button" data-action="blueprint-confirm" type="button">确认并运行</button>
                <button class="button secondary compact-button" data-action="blueprint-refresh" type="button">重新生成</button>
              `
          }
          <button class="icon-button" data-action="blueprint-dismiss" type="button" title="关闭执行蓝图">×</button>
        </div>
      </div>
      ${
        loading
          ? `<p>正在根据需求分析推荐团队、能力包、执行阶段和风险提示。</p>`
          : `
            <div class="blueprint-summary">
              <div><strong>${escapeHtml(blueprint.summary?.stage_count ?? stages.length)}</strong><span>阶段</span></div>
              <div><strong>${escapeHtml(blueprint.summary?.agent_count ?? blueprint.agents?.length ?? 0)}</strong><span>Agent</span></div>
              <div><strong>${escapeHtml(blueprint.summary?.capability_count ?? capabilities.length)}</strong><span>能力</span></div>
              <div><strong>${escapeHtml(blueprint.summary?.risk_count ?? risks.length)}</strong><span>风险</span></div>
            </div>
            <div class="blueprint-section">
              <span>推荐团队</span>
              <div class="blueprint-tags">${(blueprint.agents || []).map((agent) => `<strong>${escapeHtml(agent)}</strong>`).join("")}</div>
            </div>
            <div class="blueprint-section">
              <span>能力包</span>
              <div class="blueprint-tags">
                ${capabilities
                  .slice(0, 8)
                  .map((item) => `<strong class="${escapeHtml(item.status || "ready")}">${escapeHtml(item.name || item.id)}</strong>`)
                  .join("")}
              </div>
            </div>
            <div class="blueprint-stage-list">
              ${stages
                .slice(0, 6)
                .map(
                  (stage, index) => `
                    <div class="blueprint-stage">
                      <strong>${escapeHtml(index + 1)}</strong>
                      <div>
                        <span>${escapeHtml(stage.title)}</span>
                        <p>${escapeHtml(stage.owner || "Agent")} · ${escapeHtml(stage.description || "")}</p>
                      </div>
                    </div>
                  `,
                )
                .join("")}
            </div>
            <div class="blueprint-risks">
              ${risks
                .slice(0, 3)
                .map((risk) => `<span class="${escapeHtml(risk.level || "low")}">${escapeHtml(risk.title)}</span>`)
                .join("")}
            </div>
          `
      }
    </section>
  `;
}

function renderCapabilityRecommendation() {
  if (state.capabilityRecommendationDismissed || state.capabilityRecommendationMuted) return "";

  const recommendation = state.capabilityRecommendation || {};
  const capabilities = recommendation.capabilities || [];
  const agents = recommendation.agents || [];
  if (!agents.length && !capabilities.length) return "";

  return `
    <section class="recommend-panel">
      <div class="recommend-head">
        <div>
          <span>智能组队建议</span>
          <strong>${escapeHtml(agents.slice(0, 4).join(" / "))}</strong>
        </div>
        <div class="recommend-actions">
          <button class="button secondary compact-button" data-action="show-capabilities" type="button">查看能力</button>
          <button class="icon-button subtle" data-action="dismiss-recommendation" title="关闭智能组队建议" type="button">×</button>
        </div>
      </div>
      <div class="recommend-capabilities">
        ${capabilities
          .slice(0, 8)
          .map(
            (item) => `
              <span class="${escapeHtml(item.status || "ready")}">
                ${escapeHtml(item.name || item.id)}
              </span>
            `,
          )
          .join("")}
      </div>
      ${
        recommendation.reasons?.length
          ? `<p>${escapeHtml(recommendation.reasons[0])}</p>`
          : ""
      }
    </section>
  `;
}

function renderApprovalPanel() {
  const approval = state.approval || {};
  if (!approval.status || approval.status === "idle" || approval.status === "resolved") return "";

  const tasks = approval.tasks || [];
  const isPending = approval.status === "pending";
  return `
    <section class="approval-panel ${escapeHtml(approval.status)}">
      <div class="approval-head">
        <div>
          <span class="approval-kicker">计划审批</span>
          <h3>${escapeHtml(approval.title || "等待用户审批计划")}</h3>
        </div>
        <span class="badge ${isPending ? "warning" : approval.decision || "ready"}">
          ${isPending ? "待审批" : approvalDecisionLabel(approval.decision)}
        </span>
      </div>
      <p>${escapeHtml(approval.content || "")}</p>
      ${
        tasks.length
          ? `<div class="approval-tasks">
              ${tasks
                .slice(0, 4)
                .map(
                  (task, index) => `
                    <div class="approval-task">
                      <strong>${escapeHtml(index + 1)}</strong>
                      <span>${escapeHtml(task.title || task.id || task)}</span>
                    </div>
                  `,
                )
                .join("")}
            </div>`
          : ""
      }
      ${
        isPending
          ? `
            <textarea class="approval-comment" id="approval-comment" rows="2" placeholder="可选：给 Planner 留下审批意见"></textarea>
            <div class="approval-actions">
              <button class="button" data-action="approval-decision" data-decision="approved">批准</button>
              <button class="button secondary" data-action="approval-decision" data-decision="revise">修改</button>
              <button class="button secondary" data-action="approval-decision" data-decision="rejected">拒绝</button>
            </div>
          `
          : `<div class="approval-result">${escapeHtml(approval.comment || approvalDecisionLabel(approval.decision))}</div>`
      }
    </section>
  `;
}

function renderMessage(message) {
  const isUser = message.role === "user";
  const tone = isUser ? "user" : agentToneFromName(message.author);
  return `
    <article class="message ${isUser ? "user" : ""}">
      ${renderAgentAvatar(message.author, tone, "avatar")}
      <div class="bubble">
        <div class="message-head">
          <span class="message-author">${escapeHtml(message.author)}</span>
          <span>${escapeHtml(message.time)}</span>
        </div>
        <p class="message-text">${escapeHtml(message.content)}</p>
      </div>
    </article>
  `;
}

function renderAgentAvatar(name, tone = "lead", extraClass = "") {
  const safeTone = agentToneFromName(name, tone);
  return `
    <div class="agent-avatar ${escapeHtml(safeTone)} ${escapeHtml(extraClass)}" title="${escapeHtml(name || safeTone)}">
      <span>${escapeHtml(agentAvatarSymbol(safeTone, name))}</span>
      <i></i>
    </div>
  `;
}

function agentToneFromName(value = "", fallback = "lead") {
  const text = String(value || "").toLowerCase();
  if (text.includes("user") || text.includes("用户")) return "user";
  if (text.includes("planner") || text.includes("plan")) return "planner";
  if (text.includes("coder") || text.includes("code")) return "coder";
  if (text.includes("tester") || text.includes("test") || text.includes("verifier")) return "tester";
  if (text.includes("reviewer") || text.includes("review")) return "reviewer";
  if (text.includes("designer") || text.includes("design")) return "designer";
  if (text.includes("devops") || text.includes("deploy")) return "devops";
  if (text.includes("lead") || text.includes("supervisor")) return "lead";
  return fallback;
}

function agentAvatarSymbol(tone, name = "") {
  const symbols = {
    user: "U",
    lead: "L",
    planner: "P",
    coder: "</>",
    tester: "T",
    reviewer: "R",
    designer: "D",
    devops: "O",
  };
  return symbols[tone] || String(name || "A").slice(0, 1).toUpperCase();
}

function renderRightPanel() {
  const tabs = [
    ["tasks", "任务", state.tasks.length],
    ["team", "团队", state.team.length],
    ["capabilities", "能力", state.capabilityHub?.summary?.total ?? 0],
    ["metrics", "指标", state.metrics.toolCalls],
    ["benchmarks", "基准", state.benchmarks.length],
    ["preferences", "偏好", state.memoryProfile?.preference_count ?? 0],
  ];

  if (state.layout?.rightCollapsed) {
    return `
      <aside class="panel right-panel right-rail">
        <button class="rail-toggle" data-action="toggle-right" title="展开右侧栏">‹</button>
        ${tabs
          .map(
            ([id, label, count]) => `
              <button class="rail-nav-button ${state.rightTab === id ? "active" : ""}" data-action="side-nav" data-side="right" data-tab="${id}" title="${label}">
                <strong>${escapeHtml(count)}</strong>
                <span>${escapeHtml(label)}</span>
              </button>
            `,
          )
          .join("")}
      </aside>
    `;
  }

  return `
    <aside class="panel right-panel">
      <div class="right-tabs">
        ${tabs
          .map(
            ([id, label]) =>
              `<button class="tab-button ${state.rightTab === id ? "active" : ""}" data-action="right-tab" data-tab="${id}">${label}</button>`,
          )
          .join("")}
        <button class="tab-button panel-collapse-button" data-action="toggle-right" title="收起右侧栏">›</button>
      </div>
      <div class="content-scroll">
        ${state.rightTab === "tasks" ? renderTasks() : ""}
        ${state.rightTab === "team" ? renderTeam() : ""}
        ${state.rightTab === "capabilities" ? renderCapabilities() : ""}
        ${state.rightTab === "metrics" ? renderMetrics() : ""}
        ${state.rightTab === "benchmarks" ? renderBenchmarks() : ""}
        ${state.rightTab === "preferences" ? renderPreferences() : ""}
      </div>
    </aside>
  `;
}

function renderTasks() {
  const visibleTasks = state.tasks.filter(isVisibleTask);
  const allCompleted =
    visibleTasks.length > 0 && visibleTasks.every((task) => ["completed", "skipped"].includes(task.status));
  const archiveCompleted = state.status === "completed" && allCompleted && !state.showCompletedTasks;

  if (archiveCompleted) {
    return `
      <div class="task-list">
        <section class="task-archive-summary">
          <div>
            <strong>${escapeHtml(visibleTasks.length)}</strong>
            <span>任务已完成并归档</span>
          </div>
          <button class="button secondary compact-button" data-action="toggle-completed-tasks" type="button">查看任务</button>
        </section>
      </div>
    `;
  }

  return `
    <div class="task-list">
      ${
        state.status === "completed" && allCompleted
          ? `<section class="task-archive-summary open">
              <div>
                <strong>${escapeHtml(visibleTasks.length)}</strong>
                <span>已展开完成任务</span>
              </div>
              <button class="button secondary compact-button" data-action="toggle-completed-tasks" type="button">收起任务</button>
            </section>`
          : ""
      }
      ${visibleTasks.length ? visibleTasks.map(renderTask).join("") : `<div class="empty-mini">任务生成中，等待 Planner 补齐标题和验收点</div>`}
    </div>
  `;
}

function renderTask(task) {
  const capabilities = task.capabilities || inferTaskCapabilities(task);
  const evidence = Array.isArray(task.toolEvidence) ? task.toolEvidence : [];
  return `
    <article class="task-card ${escapeHtml(task.status || "idle")}">
      <div class="task-top">
        <div class="task-title">${escapeHtml(task.title)}</div>
        <span class="badge ${escapeHtml(task.status)}">${statusLabel(task.status)}</span>
      </div>
      <p class="task-desc">${escapeHtml(task.description)}</p>
      <div class="task-meta-row">
        <span class="panel-subtitle">负责人：${escapeHtml(task.owner)}</span>
        ${
          task.failure
            ? `<span class="task-failure">失败原因：${escapeHtml(task.failure)}</span>`
            : ""
        }
        ${
          capabilities.length
            ? `<div class="task-capabilities">
                ${capabilities
                  .slice(0, 4)
                  .map((capabilityId) => `<span>${escapeHtml(capabilityDisplayName(capabilityId))}</span>`)
                  .join("")}
              </div>`
            : ""
        }
        ${
          evidence.length
            ? `<div class="task-evidence">
                ${evidence
                  .slice(-4)
                  .map(
                    (item) => `
                      <span title="${escapeHtml(item.capabilityId || item.capability_id || "")}">
                        ${escapeHtml(item.tool || "tool")}
                      </span>
                    `,
                  )
                  .join("")}
              </div>`
            : ""
        }
      </div>
    </article>
  `;
}

function isVisibleTask(task) {
  return Boolean(String(task?.title || "").trim() || String(task?.description || "").trim());
}

function renderTeam() {
  return `
    <div class="team-list">
      <section class="conversation-team-banner">
        <div>
          <span>会话团队</span>
          <strong>${escapeHtml(state.currentConversationId || "尚未绑定会话")}</strong>
        </div>
        <button class="button secondary compact-button" data-action="refresh-conversation-team" type="button" ${state.currentConversationId ? "" : "disabled"}>重新推荐</button>
      </section>
      ${renderAgentCreateForm()}
      ${state.team.map((member, index) => renderTeamMember(member, index)).join("")}
    </div>
  `;
}

function renderAgentCreateForm() {
  const capabilityOptions = getCapabilityOptions();
  return `
    <form class="agent-create" id="agent-create-form">
      <div class="agent-create-row">
        <input id="agent-name" placeholder="Agent 名称" maxlength="40" />
        <input id="agent-role" placeholder="角色，如 reviewer" maxlength="40" />
      </div>
      <textarea id="agent-goal" rows="2" placeholder="这个 Agent 负责什么？"></textarea>
      <div class="agent-capability-picker">
        <div class="agent-create-label">
          <span>能力包</span>
          <strong>${escapeHtml(capabilityOptions.length)} 项</strong>
        </div>
        <div class="capability-choice-list">
          ${capabilityOptions
            .map(
              (item) => `
                <label class="capability-choice">
                  <input type="checkbox" name="agent-capability" value="${escapeHtml(item.id)}" />
                  <span>${escapeHtml(item.name)}</span>
                  <small>${escapeHtml(capabilityKindLabel(item.kind))}</small>
                </label>
              `,
            )
            .join("")}
        </div>
      </div>
      <div class="agent-create-row">
        <input id="agent-tools" placeholder="补充工具，用逗号分隔" />
        <button class="button secondary" type="submit">添加</button>
      </div>
    </form>
  `;
}

function renderTeamMember(member, index = 0) {
  return `
    <article class="team-member">
      ${renderAgentAvatar(member.name || member.role, member.tone, "agent-dot")}
      <div class="team-member-body">
        <div class="agent-name">${escapeHtml(member.name)}</div>
        <div class="agent-role">${escapeHtml(member.role)}</div>
        ${member.goal ? `<p class="agent-goal">${escapeHtml(member.goal)}</p>` : ""}
        <div class="agent-card-meta">
          ${(member.tools || []).slice(0, 4).map((tool) => `<span>${escapeHtml(tool)}</span>`).join("")}
        </div>
        ${
          member.capabilities?.length
            ? `<div class="agent-capability-meta">
                ${member.capabilities
                  .slice(0, 4)
                  .map((capabilityId) => `<span>${escapeHtml(capabilityDisplayName(capabilityId))}</span>`)
                  .join("")}
              </div>`
            : ""
        }
        ${member.lastAction ? `<div class="agent-last">${escapeHtml(member.lastAction)}</div>` : ""}
      </div>
      <div class="agent-card-actions">
        <span class="badge ${escapeHtml(member.status)}">${statusLabel(member.status)}</span>
        <button class="icon-button subtle" data-action="remove-team-member" data-index="${escapeHtml(index)}" title="移除该 Agent" type="button" ${state.team.length <= 1 ? "disabled" : ""}>×</button>
      </div>
    </article>
  `;
}

function renderCapabilities() {
  const hub = state.capabilityHub || {};
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
      <form class="skill-import-panel" id="skill-import-form">
        <div>
          <strong>导入自定义 Skill</strong>
          <span>写入当前项目的 .nanocursor/skills，刷新后自动进入能力中心。</span>
        </div>
        <input id="skill-name-input" placeholder="Skill 名称，例如 api-review" />
        <textarea id="skill-content-input" rows="3" placeholder="粘贴 SKILL.md 内容，或写一段用途说明"></textarea>
        <button class="button secondary compact-button" type="submit">导入</button>
      </form>
      <div class="capability-groups">
        ${groups.map(renderCapabilityGroup).join("")}
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

function renderMetrics() {
  const metrics = [
    ["任务", state.metrics.tasks],
    ["文件", state.metrics.files],
    ["工具调用", state.metrics.toolCalls],
    ["Token", state.metrics.tokens],
    ["验证", state.metrics.tests],
  ];

  return `
    <div class="metric-list">
      ${metrics
        .map(
          ([label, value]) => `
            <div class="metric-item">
              <span class="metric-label">${escapeHtml(label)}</span>
              <span class="metric-value">${escapeHtml(value)}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderBenchmarks() {
  return `
    <div class="benchmark-list">
      ${state.benchmarks.map(renderBenchmarkCard).join("")}
    </div>
  `;
}

function renderBenchmarkCard(item) {
  return `
    <article class="benchmark-card">
      <div class="benchmark-head">
        <span class="artifact-kind">${escapeHtml(item.category)}</span>
        <span class="badge ${escapeHtml(item.difficulty)}">${escapeHtml(item.difficulty)}</span>
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.description)}</p>
      <div class="benchmark-checks">
        ${(item.acceptance_criteria || []).slice(0, 4).map((check) => `<span>${escapeHtml(check)}</span>`).join("")}
      </div>
      <button class="button secondary" data-action="run-benchmark" data-benchmark-id="${escapeHtml(item.id)}">运行基准</button>
    </article>
  `;
}

function renderPreferences() {
  const profile = state.memoryProfile || {};
  const buckets = profile.buckets || [];
  return `
    <div class="preference-panel">
      <form class="preference-create" id="preference-create-form">
        <select id="preference-type">
          <option value="code_style">代码风格</option>
          <option value="ui_style">UI 风格</option>
          <option value="tech_stack">常用技术栈</option>
          <option value="testing">测试偏好</option>
          <option value="file_organization">文件组织</option>
        </select>
        <textarea id="preference-content" rows="2" placeholder="记录一个你希望 nanoCursor 记住的偏好"></textarea>
        <button class="button secondary" type="submit">保存偏好</button>
      </form>
      <section class="preference-summary">
        <div><strong>${escapeHtml(profile.preference_count ?? 0)}</strong><span>偏好记忆</span></div>
        <div><strong>${escapeHtml(profile.high_importance_count ?? 0)}</strong><span>高重要性</span></div>
        <div><strong>${escapeHtml(profile.total_memories ?? 0)}</strong><span>全部记忆</span></div>
      </section>
      ${profile.prompt_context ? `<pre class="preference-context">${escapeHtml(profile.prompt_context)}</pre>` : ""}
      <div class="preference-buckets">
        ${buckets.map(renderPreferenceBucket).join("")}
      </div>
    </div>
  `;
}

function renderPreferenceBucket(bucket) {
  const memories = bucket.memories || [];
  return `
    <article class="preference-bucket">
      <div class="preference-head">
        <div>
          <h3>${escapeHtml(bucket.label)}</h3>
          <p>${escapeHtml(bucket.description)}</p>
        </div>
        <span class="badge ${escapeHtml(bucket.confidence)}">${preferenceConfidenceLabel(bucket.confidence)}</span>
      </div>
      <div class="preference-memory-list">
        ${
          memories.length
            ? memories
                .map(
                  (memory) => `
                    <div class="preference-memory">
                      <span>${escapeHtml(memory.content)}</span>
                      <strong>${escapeHtml(memory.importance)}</strong>
                    </div>
                  `,
                )
                .join("")
            : `<div class="empty-mini">暂无偏好</div>`
        }
      </div>
    </article>
  `;
}

function preferenceConfidenceLabel(confidence) {
  const labels = {
    high: "高可信",
    medium: "已记录",
    empty: "待学习",
  };
  return labels[confidence] || confidence || "未知";
}

function renderBottomPanel() {
  const tabs = [
    ["timeline", "时间线"],
    ["diff", "Diff"],
    ["artifacts", "交付物"],
    ["recovery", "恢复"],
    ["preview", "预览"],
    ["report", "报告"],
  ];
  const collapsed = state.layout?.bottomCollapsed;

  return `
    <section class="panel bottom-panel ${collapsed ? "collapsed" : ""}">
      <div class="bottom-tabs ${collapsed ? "compact" : ""}">
        ${tabs
          .map(
            ([id, label]) =>
              `<button class="tab-button ${state.activeTab === id ? "active" : ""}" data-action="bottom-tab" data-tab="${id}">${label}</button>`,
          )
          .join("")}
        ${collapsed ? `<div class="bottom-summary compact">${renderBottomSummary()}</div>` : ""}
        <button class="tab-button panel-collapse-button bottom-collapse-button" data-action="toggle-bottom" title="${collapsed ? "展开证据区" : "收起证据区"}">${collapsed ? "展开" : "收起"}</button>
      </div>
      ${
        collapsed
          ? ""
          : `<div class="bottom-content">${renderBottomContent()}</div>`
      }
    </section>
  `;
}

function renderBottomSummary() {
  const diffCount = state.diffFiles?.length || state.report.changedFiles?.length || 0;
  const score = state.artifactCenter?.summary?.score ?? "--";
  const coverage = Math.round((state.report.traceability?.coverageRate || state.artifactCenter?.summary?.coverage_rate || 0) * 100);
  const risks = (state.recoveryCenter?.summary?.risk_count ?? collectReportRisks().length ?? 0);
  const items = [
    ["Diff", `${diffCount} 文件`],
    ["报告", `${score} 分`],
    ["覆盖", `${coverage}%`],
    ["风险", `${risks} 项`],
  ];

  return items
    .map(
      ([label, value]) => `
        <div class="summary-chip">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderBottomContent() {
  if (state.activeTab === "timeline") return renderTimeline();
  if (state.activeTab === "artifacts") return renderArtifacts();
  if (state.activeTab === "recovery") return renderRecovery();
  if (state.activeTab === "preview") return renderPreview();
  if (state.activeTab === "report") return renderReport();
  return renderDiffView();
}

function renderDiffView() {
  syncDiffFiles();
  const files = state.diffFiles || [];
  const selected = files.find((file) => file.path === state.selectedDiffFile) || files[0];
  if (!files.length) {
    return `<div class="empty">暂无 Diff 记录</div>`;
  }

  return `
    <div class="diff-browser">
      <aside class="diff-file-list">
        <div class="diff-file-list-head">
          <strong>${escapeHtml(files.length)}</strong>
          <span>变更文件</span>
        </div>
        ${files.map(renderDiffFileButton).join("")}
      </aside>
      <section class="diff-detail">
        <header class="diff-detail-head">
          <div>
            <span class="artifact-kind">${escapeHtml(selected.changeType || "modified")}</span>
            <h3 title="${escapeHtml(selected.path)}">${escapeHtml(shortPath(selected.path))}</h3>
          </div>
          <div class="diff-stats">
            <span class="diff-add">+${escapeHtml(selected.additions || 0)}</span>
            <span class="diff-del">-${escapeHtml(selected.deletions || 0)}</span>
          </div>
        </header>
        <pre class="diff-view">${escapeHtml(selected.diff || "该文件暂无可展示的 Diff 片段。")}</pre>
      </section>
    </div>
  `;
}

function renderDiffFileButton(file) {
  const active = file.path === state.selectedDiffFile ? "active" : "";
  return `
    <button class="diff-file-item ${active}" data-action="select-diff-file" data-path="${escapeHtml(file.path)}">
      <span class="diff-file-name" title="${escapeHtml(file.path)}">${escapeHtml(shortPath(file.path))}</span>
      <span class="diff-file-meta">
        <span class="diff-add">+${escapeHtml(file.additions || 0)}</span>
        <span class="diff-del">-${escapeHtml(file.deletions || 0)}</span>
      </span>
    </button>
  `;
}

function renderArtifacts() {
  const center = state.artifactCenter;
  const artifacts = center?.artifacts || [];
  if (!artifacts.length) {
    return `<div class="empty">暂无交付物索引</div>`;
  }

  const summary = center.summary || {};
  return `
    <div class="artifact-center">
      <section class="artifact-summary">
        <div class="artifact-score">
          <span>${escapeHtml(summary.score ?? "--")}</span>
          <small>交付评分</small>
        </div>
        <div class="artifact-summary-grid">
          <div><strong>${escapeHtml(summary.artifact_count ?? artifacts.length)}</strong><span>交付物</span></div>
          <div><strong>${escapeHtml(summary.ready_count ?? 0)}</strong><span>就绪</span></div>
          <div><strong>${escapeHtml(summary.warning_count ?? 0)}</strong><span>提醒</span></div>
          <div><strong>${escapeHtml(Math.round((summary.coverage_rate || 0) * 100))}%</strong><span>需求覆盖</span></div>
        </div>
      </section>
      <section class="artifact-grid">
        ${artifacts.map(renderArtifactCard).join("")}
      </section>
    </div>
  `;
}

function renderArtifactCard(item) {
  return `
    <article class="artifact-card">
      <div class="artifact-card-head">
        <span class="artifact-kind">${escapeHtml(item.kind)}</span>
        <span class="badge ${escapeHtml(item.status)}">${artifactStatusLabel(item.status)}</span>
      </div>
      <h3>${escapeHtml(item.label)}</h3>
      <p>${escapeHtml(item.summary || "")}</p>
      <div class="artifact-meta">
        ${item.count === null || item.count === undefined ? "" : `<span>数量 ${escapeHtml(item.count)}</span>`}
        ${item.path ? `<span title="${escapeHtml(item.path)}">${escapeHtml(shortPath(item.path))}</span>` : ""}
      </div>
    </article>
  `;
}

function artifactStatusLabel(status) {
  const labels = {
    ready: "就绪",
    warning: "提醒",
    missing: "缺失",
    empty: "暂无",
    incomplete: "未完整",
  };
  return labels[status] || status || "未知";
}

function renderRecovery() {
  const center = state.recoveryCenter || {};
  const summary = center.summary || {};
  const points = center.recovery_points || [];
  const risks = center.risks || [];
  const actions = center.actions || [];
  return `
    <div class="recovery-center">
      <section class="recovery-summary">
        <div class="recovery-status ${escapeHtml(center.status || "unknown")}">
          <strong>${recoveryStatusLabel(center.status)}</strong>
          <span>安全状态</span>
        </div>
        <div class="artifact-summary-grid">
          <div><strong>${escapeHtml(summary.snapshot_count ?? 0)}</strong><span>快照</span></div>
          <div><strong>${escapeHtml(summary.backup_count ?? 0)}</strong><span>备份</span></div>
          <div><strong>${escapeHtml(summary.risk_count ?? 0)}</strong><span>风险</span></div>
          <div><strong>${escapeHtml(summary.high_risk_count ?? 0)}</strong><span>高风险</span></div>
        </div>
      </section>
      <section class="recovery-action-panel">
        <div class="recovery-section-head">
          <h3>推荐修复路径</h3>
          <span>${escapeHtml(actions.length || 0)} 步</span>
        </div>
        <div class="recovery-action-list">
          ${actions.length ? actions.map(renderRecoveryAction).join("") : `<div class="recovery-ok">暂无需要处理的恢复动作</div>`}
        </div>
      </section>
      <section class="recovery-grid">
        <div>
          <h3>恢复点</h3>
          <div class="recovery-list">
            ${points.length ? points.map(renderRecoveryPoint).join("") : `<div class="empty-mini">暂无快照或备份</div>`}
          </div>
        </div>
        <div>
          <h3>风险和诊断</h3>
          <div class="recovery-list">
            ${risks.length ? risks.map(renderRecoveryRisk).join("") : `<div class="recovery-ok">未发现阻塞风险</div>`}
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderRecoveryAction(action) {
  return `
    <article class="recovery-action ${escapeHtml(action.priority || "low")} ${action.enabled ? "" : "disabled"}">
      <div>
        <span>${escapeHtml(recoveryActionTypeLabel(action.action_type))}</span>
        <strong>${escapeHtml(action.title)}</strong>
        <p>${escapeHtml(action.detail || "")}</p>
      </div>
      <em>${escapeHtml(recoveryPriorityLabel(action.priority))}</em>
    </article>
  `;
}

function renderRecoveryPoint(point) {
  return `
    <article class="recovery-card">
      <div class="recovery-card-head">
        <span class="artifact-kind">${escapeHtml(point.kind)}</span>
        <span class="badge ${escapeHtml(point.status)}">${escapeHtml(point.status || "available")}</span>
      </div>
      <h4>${escapeHtml(point.label || point.id)}</h4>
      <p>${escapeHtml(point.detail || point.reason || "")}</p>
      <div class="artifact-meta">
        ${point.target_path ? `<span>${escapeHtml(point.target_path)}</span>` : ""}
        ${point.size ? `<span>${escapeHtml(point.size)} bytes</span>` : ""}
      </div>
    </article>
  `;
}

function renderRecoveryRisk(risk) {
  return `
    <article class="recovery-card risk-${escapeHtml(risk.severity)}">
      <div class="recovery-card-head">
        <span class="artifact-kind">${escapeHtml(risk.severity)}</span>
        <span class="badge ${escapeHtml(risk.severity)}">${riskSeverityLabel(risk.severity)}</span>
      </div>
      <h4>${escapeHtml(risk.title)}</h4>
      <p>${escapeHtml(risk.detail || "")}</p>
    </article>
  `;
}

function recoveryStatusLabel(status) {
  const labels = {
    safe: "安全",
    review: "需复核",
    attention: "需处理",
    unprotected: "未保护",
  };
  return labels[status] || status || "未知";
}

function riskSeverityLabel(severity) {
  const labels = {
    high: "高",
    medium: "中",
    low: "低",
  };
  return labels[severity] || severity || "未知";
}

function recoveryPriorityLabel(priority) {
  const labels = {
    high: "优先",
    medium: "建议",
    low: "可选",
  };
  return labels[priority] || priority || "建议";
}

function recoveryActionTypeLabel(actionType) {
  const labels = {
    inspect_timeline: "时间线",
    review_diff: "Diff",
    quality_gate: "质量",
    recovery_point: "恢复点",
    snapshot: "快照",
    continue: "继续",
  };
  return labels[actionType] || actionType || "动作";
}

function renderTimeline() {
  const replayControls = renderReplayControls();
  const timelineBody = state.events.length
    ? `
      <div class="timeline">
        ${state.events
          .map(
            (event) => `
              <article class="event-item ${eventKind(event.type)}">
                <span class="event-line"></span>
                <div>
                  <div class="event-title">${escapeHtml(event.title || event.type)}</div>
                  <div class="event-content">${escapeHtml(event.content || "")}</div>
                  ${renderEventCapabilityTrace(event)}
                </div>
                <time class="event-time">${escapeHtml(event.time || "")}</time>
              </article>
            `,
          )
          .join("")}
      </div>
    `
    : `<div class="empty">等待事件流</div>`;

  return `
    <div class="timeline-shell">
      ${replayControls}
      ${timelineBody}
    </div>
  `;
}

function renderReplayControls() {
  const replay = state.replay || {};
  const total = replay.events?.length || 0;
  const index = Math.min(replay.index || 0, total);
  const percent = total ? Math.round((index / total) * 100) : 0;
  const canReplay = total > 0;
  const isPlaying = replay.status === "playing";
  const playLabel = index >= total ? "重放" : "播放";

  return `
    <div class="replay-bar">
      <div class="replay-status">
        <strong>${replayStatusLabel(replay.status)}</strong>
        <span>${escapeHtml(index)} / ${escapeHtml(total)} 事件</span>
      </div>
      <div class="replay-progress" aria-hidden="true">
        <span style="width: ${escapeHtml(percent)}%"></span>
      </div>
      <div class="replay-actions">
        <button class="button secondary" data-action="replay-play" ${!canReplay || isPlaying ? "disabled" : ""}>${playLabel}</button>
        <button class="button secondary" data-action="replay-pause" ${!isPlaying ? "disabled" : ""}>暂停</button>
        <button class="button secondary" data-action="replay-reset" ${!canReplay ? "disabled" : ""}>复位</button>
        <label class="replay-speed">
          <span>速度</span>
          <select data-action="replay-speed" ${!canReplay ? "disabled" : ""}>
            ${[0.5, 1, 2, 4]
              .map(
                (speed) =>
                  `<option value="${speed}" ${Number(replay.speed || 1) === speed ? "selected" : ""}>${speed}x</option>`,
              )
              .join("")}
          </select>
        </label>
      </div>
    </div>
  `;
}

function renderPreview() {
  const previewUrl = state.previewUrl || "localhost:5173/demo-todo";
  return `
    <div class="preview-frame">
      <div class="preview-surface">
        <div class="preview-top">
          <span class="browser-dot"></span>
          <span class="browser-dot"></span>
          <span class="browser-dot"></span>
          <span class="panel-subtitle">${escapeHtml(previewUrl)}</span>
        </div>
        <div class="preview-body">
          <input class="preview-input" value="搜索任务：localStorage" readonly />
          <div class="preview-list">
            <div class="preview-row"><span class="badge completed">完成</span><span>新增 Todo 输入框</span><button class="button secondary">删除</button></div>
            <div class="preview-row"><span class="badge completed">完成</span><span>保存到 localStorage</span><button class="button secondary">删除</button></div>
            <div class="preview-row"><span class="badge pending">待处理</span><span>补充自动化测试</span><button class="button secondary">删除</button></div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderReport() {
  const score = getArtifactPayload("score");
  const quality = getArtifactPayload("quality");
  const riskItems = collectReportRisks();
  const changedFiles = collectChangedFiles();
  const nextSteps = collectNextSteps();
  const summary = reportSummaryText();
  const coverage = state.report.traceability?.coverageRate || state.artifactCenter?.summary?.coverage_rate || 0;
  const coveragePercent = Math.round(coverage * 100);

  return `
    <article class="report structured-report">
      <section class="report-hero">
        <div>
          <span class="artifact-kind">Delivery Report</span>
          <h3>交付证据总览</h3>
          <p>${escapeHtml(summary)}</p>
        </div>
        <div class="report-score">
          <strong>${escapeHtml(score?.score ?? state.artifactCenter?.summary?.score ?? "--")}</strong>
          <span>${escapeHtml(deliveryLevelLabel(score?.level))}</span>
        </div>
      </section>

      <section class="report-kpis">
        ${renderReportKpi("需求覆盖", `${coveragePercent}%`, `${state.report.traceability?.coveredCount || 0} / ${state.report.traceability?.totalCount || 0}`)}
        ${renderReportKpi("质量门禁", qualityStatusLabel(quality?.status), `${quality?.passed_count ?? 0} 通过 · ${quality?.warning_count ?? 0} 提醒`)}
        ${renderReportKpi("变更文件", changedFiles.length, changedFiles.length ? "已生成 Diff" : "暂无文件变更")}
        ${renderReportKpi("风险", riskItems.length, riskItems.length ? "需要复核" : "未发现阻塞风险")}
      </section>

      <section class="report-grid">
        ${renderReportSection("变更文件", changedFiles, renderChangedFileEvidence, "暂无变更文件")}
        ${renderQualityEvidence(quality)}
        ${renderReportSection("风险与下一步", riskItems.length ? riskItems : nextSteps, renderTextEvidence, "未发现阻塞风险")}
      </section>

      ${renderTraceability()}
      ${state.report.markdown ? renderRawReport(state.report.markdown) : ""}
    </article>
  `;
}

function getArtifact(id) {
  return (state.artifactCenter?.artifacts || []).find((item) => item.id === id);
}

function getArtifactPayload(id) {
  return getArtifact(id)?.payload || null;
}

function renderReportKpi(label, value, detail) {
  return `
    <div class="report-kpi">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `;
}

function reportSummaryText() {
  if (state.report.summary && state.report.summary !== "Loaded saved delivery report.") {
    return state.report.summary;
  }
  const fromMarkdown = markdownSection(state.report.markdown, "Summary");
  if (fromMarkdown.length) return fromMarkdown[0];
  return "本次运行已归档需求、任务、变更文件、质量门禁、风险和交付报告。";
}

function collectChangedFiles() {
  const artifactFiles = getArtifactPayload("changed_files")?.changed_files || [];
  const files = artifactFiles.length ? artifactFiles : state.report.changedFiles;
  return files
    .map((file) => (typeof file === "string" ? { path: file, change_type: "modified" } : file))
    .filter((file) => file?.path);
}

function collectReportRisks() {
  const artifactRisks = getArtifactPayload("risks")?.risks || [];
  const reportRisks = state.report.risks || [];
  return [...artifactRisks, ...reportRisks].filter(Boolean);
}

function collectNextSteps() {
  const steps = markdownSection(state.report.markdown, "Next Steps");
  return steps.length ? steps : ["补充比赛演示材料。", "继续增强报告结构化展示和 Diff 审查体验。"];
}

function markdownSection(markdown, heading) {
  if (!markdown) return [];
  const lines = markdown.split("\n");
  const start = lines.findIndex((line) => line.trim().toLowerCase() === `## ${heading}`.toLowerCase());
  if (start < 0) return [];
  const items = [];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith("## ")) break;
    const clean = line.replace(/^[-*]\s*/, "").replace(/^#+\s*/, "").trim();
    if (clean) items.push(clean);
  }
  return items;
}

function renderReportSection(title, items, renderer, emptyText) {
  return `
    <section class="report-card">
      <div class="report-card-head">
        <h4>${escapeHtml(title)}</h4>
        <span class="panel-subtitle">${escapeHtml(items.length)} 项</span>
      </div>
      <div class="report-evidence-list">
        ${items.length ? items.slice(0, 8).map(renderer).join("") : `<div class="empty-mini">${escapeHtml(emptyText)}</div>`}
      </div>
    </section>
  `;
}

function renderChangedFileEvidence(file) {
  return `
    <div class="report-evidence">
      <strong>${escapeHtml(shortPath(file.path))}</strong>
      <span>${escapeHtml(file.change_type || file.status || "modified")}</span>
    </div>
  `;
}

function renderTextEvidence(item) {
  return `
    <div class="report-evidence text-only">
      <span>${escapeHtml(item)}</span>
    </div>
  `;
}

function renderQualityEvidence(quality) {
  const checks = quality?.checks || [];
  return `
    <section class="report-card">
      <div class="report-card-head">
        <h4>质量门禁</h4>
        <span class="badge ${escapeHtml(quality?.status || "unknown")}">${qualityStatusLabel(quality?.status)}</span>
      </div>
      <div class="quality-summary">
        <div><strong>${escapeHtml(quality?.passed_count ?? 0)}</strong><span>通过</span></div>
        <div><strong>${escapeHtml(quality?.warning_count ?? 0)}</strong><span>提醒</span></div>
        <div><strong>${escapeHtml(quality?.failed_count ?? 0)}</strong><span>失败</span></div>
      </div>
      <div class="report-evidence-list">
        ${
          checks.length
            ? checks
                .slice(0, 6)
                .map(
                  (check) => `
                    <div class="report-evidence">
                      <strong>${escapeHtml(check.label || check.id)}</strong>
                      <span>${escapeHtml(qualityCheckStatusLabel(check.status))}</span>
                    </div>
                  `,
                )
                .join("")
            : `<div class="empty-mini">暂无质量检查项</div>`
        }
      </div>
    </section>
  `;
}

function renderRawReport(markdown) {
  return `
    <details class="raw-report">
      <summary>查看原始 Markdown 报告</summary>
      <pre class="report-markdown">${escapeHtml(markdown)}</pre>
    </details>
  `;
}

function deliveryLevelLabel(level) {
  const labels = {
    excellent: "优秀",
    good: "良好",
    acceptable: "可接受",
    weak: "需改进",
  };
  return labels[level] || level || "交付评分";
}

function qualityStatusLabel(status) {
  const labels = {
    passed: "通过",
    warning: "提醒",
    failed: "失败",
  };
  return labels[status] || status || "未知";
}

function qualityCheckStatusLabel(status) {
  const labels = {
    passed: "通过",
    warning: "提醒",
    failed: "失败",
  };
  return labels[status] || status || "未知";
}

function renderTraceability() {
  const traceability = state.report.traceability;
  if (!traceability?.requirements?.length) {
    return "";
  }

  const percent = Math.round((traceability.coverageRate || 0) * 100);
  return `
    <section class="traceability">
      <div class="traceability-head">
        <div>
          <h4>需求追踪矩阵</h4>
          <p>${escapeHtml(traceability.coveredCount || 0)} / ${escapeHtml(traceability.totalCount || 0)} 个需求已覆盖</p>
        </div>
        <span class="score-chip">${escapeHtml(percent)}%</span>
      </div>
      <div class="traceability-list">
        ${traceability.requirements.map(renderTraceabilityItem).join("")}
      </div>
    </section>
  `;
}

function renderTraceabilityItem(item) {
  return `
    <article class="trace-row">
      <div class="trace-main">
        <div class="trace-title">${escapeHtml(item.id)} · ${escapeHtml(item.title)}</div>
        <span class="badge ${escapeHtml(item.status)}">${traceabilityStatusLabel(item.status)}</span>
      </div>
      <div class="trace-columns">
        <span>任务：${escapeHtml((item.tasks || []).join(", ") || "未关联")}</span>
        <span>文件：${escapeHtml((item.files || []).join(", ") || "未关联")}</span>
        <span>验证：${escapeHtml((item.tests || []).join(", ") || "未关联")}</span>
      </div>
    </article>
  `;
}

function traceabilityStatusLabel(status) {
  const labels = {
    covered: "已覆盖",
    partial: "部分覆盖",
    missing: "未覆盖",
  };
  return labels[status] || status || "未知";
}

function mapTraceability(traceability) {
  return {
    source: traceability.source || "generated",
    coverageRate: traceability.coverage_rate || 0,
    totalCount: traceability.total_count || 0,
    coveredCount: traceability.covered_count || 0,
    partialCount: traceability.partial_count || 0,
    missingCount: traceability.missing_count || 0,
    requirements: traceability.requirements || [],
  };
}

function setTraceability(traceability) {
  state.report.traceability = mapTraceability(traceability);
  state.report.requirements = state.report.traceability.requirements.map(
    (item) => `${item.id}: ${item.title}`,
  );
}

function bindEvents() {
  document.querySelectorAll("[data-action='right-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      state.rightTab = button.dataset.tab;
      render();
    });
  });

  document.querySelectorAll("[data-action='left-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      state.leftTab = button.dataset.tab;
      render();
    });
  });

  document.querySelectorAll("[data-action='side-nav']").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.side === "left") {
        state.leftTab = button.dataset.tab || state.leftTab;
        state.layout.sidebarCollapsed = false;
      } else {
        state.rightTab = button.dataset.tab || state.rightTab;
        state.layout.rightCollapsed = false;
      }
      saveLayoutPreference();
      render();
    });
  });

  document.querySelectorAll("[data-action='bottom-tab']").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      state.layout.bottomCollapsed = false;
      saveLayoutPreference();
      render();
    });
  });

  document.querySelector("[data-action='toggle-sidebar']")?.addEventListener("click", () => {
    state.layout.sidebarCollapsed = !state.layout.sidebarCollapsed;
    saveLayoutPreference();
    render();
  });

  document.querySelector("[data-action='toggle-right']")?.addEventListener("click", () => {
    state.layout.rightCollapsed = !state.layout.rightCollapsed;
    saveLayoutPreference();
    render();
  });

  document.querySelector("[data-action='toggle-bottom']")?.addEventListener("click", () => {
    state.layout.bottomCollapsed = !state.layout.bottomCollapsed;
    saveLayoutPreference();
    render();
  });

  document.querySelectorAll("[data-action='new-session']").forEach((button) => {
    button.addEventListener("click", async () => {
      await startNewSession();
    });
  });

  document.querySelector("#workspace-input")?.addEventListener("input", (event) => {
    state.workspaceInput = event.target.value;
  });

  document.querySelector("#workspace-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await openWorkspace();
  });

  document.querySelectorAll("[data-action='select-run']").forEach((button) => {
    button.addEventListener("click", async () => {
      await restoreRun(button.dataset.runId);
    });
  });

  document.querySelectorAll("[data-action='select-diff-file']").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDiffFile = button.dataset.path;
      render();
    });
  });

  document.querySelector("[data-action='replay-play']")?.addEventListener("click", () => {
    startReplay();
  });

  document.querySelector("[data-action='replay-pause']")?.addEventListener("click", () => {
    pauseReplay();
  });

  document.querySelector("[data-action='replay-reset']")?.addEventListener("click", () => {
    resetReplayToStart();
  });

  document.querySelector("[data-action='replay-speed']")?.addEventListener("change", (event) => {
    setReplaySpeed(Number(event.target.value) || 1);
  });

  document.querySelectorAll("[data-action='approval-decision']").forEach((button) => {
    button.addEventListener("click", async () => {
      await submitApprovalDecision(button.dataset.decision);
    });
  });

  document.querySelector("[data-action='reset-demo']")?.addEventListener("click", () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    stopReplayTimer();
    seenEventIds = new Set();
    const preservedLayout = structuredClone(state.layout);
    Object.assign(state, structuredClone(demoState));
    state.layout = preservedLayout;
    saveLayoutPreference();
    render();
  });

  document.querySelector("[data-action='sync-data']")?.addEventListener("click", async () => {
    await refreshWorkspaceData({ allowEmpty: true, announce: true });
    await loadWorkspaceOverview();
    render();
  });

  document.querySelector("[data-action='refresh-project-overview']")?.addEventListener("click", async () => {
    await loadWorkspaceOverview();
    addTimelineEvent({
      type: "workspace_overview",
      title: "项目概览已同步",
      content: state.projectOverview?.workspace_dir || state.workspaceDir || "当前工作区",
    });
  });

  document.querySelector("[data-action='demo-run']")?.addEventListener("click", async () => {
    await runPrompt(state.prompt, { demo: true });
  });

  document.querySelector("[data-action='copy-report']")?.addEventListener("click", async () => {
    const reportText = buildReportText();
    await navigator.clipboard?.writeText(reportText);
    addTimelineEvent({
      type: "report_ready",
      title: "报告已复制",
      content: "交付报告已写入剪贴板。",
    });
  });

  document.querySelector("[data-action='show-capabilities']")?.addEventListener("click", () => {
    state.rightTab = "capabilities";
    state.layout.rightCollapsed = false;
    saveLayoutPreference();
    render();
  });

  document.querySelector("[data-action='dismiss-recommendation']")?.addEventListener("click", () => {
    state.capabilityRecommendationDismissed = true;
    state.capabilityRecommendationMuted = true;
    sessionStorage.setItem(RECOMMENDATION_MUTED_KEY, "1");
    render();
  });

  document.querySelector("[data-action='toggle-completed-tasks']")?.addEventListener("click", () => {
    state.showCompletedTasks = !state.showCompletedTasks;
    render();
  });

  document.querySelector("[data-action='blueprint-confirm']")?.addEventListener("click", async () => {
    const prompt = state.runBlueprint?.prompt || state.prompt;
    if (prompt) await runPrompt(prompt, { blueprintConfirmed: true });
  });

  document.querySelector("[data-action='blueprint-refresh']")?.addEventListener("click", async () => {
    await prepareRunBlueprint(state.prompt);
  });

  document.querySelector("[data-action='blueprint-dismiss']")?.addEventListener("click", () => {
    state.runBlueprint = blankRunBlueprint();
    render();
  });

  document.querySelector("#prompt-input")?.addEventListener("input", (event) => {
    state.prompt = event.target.value;
    if (!state.capabilityRecommendationMuted) {
      state.capabilityRecommendationDismissed = false;
    }
    scheduleCapabilityRecommendation(state.prompt);
  });

  document.querySelector("#prompt-input")?.addEventListener("blur", () => {
    if (recommendationRenderDeferred) {
      recommendationRenderDeferred = false;
      render();
    }
  });

  document.querySelector("#prompt-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    recommendationRenderDeferred = false;
    const input = document.querySelector("#prompt-input");
    const prompt = input.value.trim();
    if (prompt) {
      prepareRunBlueprint(prompt);
    }
  });

  document.querySelector("#agent-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await createCustomAgent();
  });

  document.querySelector("[data-action='refresh-conversation-team']")?.addEventListener("click", async () => {
    await refreshConversationTeam(state.prompt);
  });

  document.querySelectorAll("[data-action='remove-team-member']").forEach((button) => {
    button.addEventListener("click", async () => {
      await removeTeamMember(Number(button.dataset.index));
    });
  });

  document.querySelector("#skill-import-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await importCustomSkill();
  });

  document.querySelectorAll("[data-action='run-benchmark']").forEach((button) => {
    button.addEventListener("click", async () => {
      await runBenchmark(button.dataset.benchmarkId);
    });
  });

  document.querySelector("#preference-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await createPreference();
  });
}

function scrollToLatestMessage() {
  const list = document.querySelector("#message-list");
  if (list) {
    list.scrollTop = list.scrollHeight;
  }
}

function addTimelineEvent(event) {
  state.events.push({
    time: nowTime(),
    ...event,
  });
  render();
}

function addMessage(message) {
  state.messages.push({
    time: nowTime(),
    ...message,
  });
}

function blankReport() {
  return {
    summary: "",
    markdown: "",
    requirements: [],
    changedFiles: [],
    risks: [],
    traceability: {
      source: "history",
      coverageRate: 0,
      totalCount: 0,
      coveredCount: 0,
      partialCount: 0,
      missingCount: 0,
      requirements: [],
    },
  };
}

function blankArtifactCenter(status = "") {
  return {
    status,
    summary: {},
    artifacts: [],
  };
}

function blankRecoveryCenter(status = "") {
  return {
    status,
    summary: {},
    recovery_points: [],
    risks: [],
    actions: [],
  };
}

function blankApproval() {
  return structuredClone(demoState.approval);
}

function blankRunBlueprint() {
  return {
    status: "idle",
    prompt: "",
    title: "",
    agents: [],
    capabilities: [],
    stages: [],
    risks: [],
    reasons: [],
    summary: {},
  };
}

function normalizeChangedFile(file) {
  if (typeof file === "string") {
    return { path: file, change_type: "modified" };
  }
  return {
    path: file?.path || file?.new_path || file?.old_path || "",
    status: file?.status || "",
    change_type: file?.change_type || file?.changeType || "modified",
  };
}

function stripDiffPathPrefix(path) {
  return String(path || "").replace(/^"|"$/g, "").replace(/^[ab]\//, "");
}

function parseDiffHeader(line) {
  const match = line.match(/^diff --git (.+?) (.+)$/);
  if (!match) return null;
  const oldPath = stripDiffPathPrefix(match[1]);
  const newPath = stripDiffPathPrefix(match[2]);
  return { oldPath, newPath, path: newPath || oldPath };
}

function parseUnifiedDiff(diff, changedFiles = []) {
  const normalizedFiles = changedFiles.map(normalizeChangedFile).filter((file) => file.path);
  const fileMeta = new Map(normalizedFiles.map((file) => [file.path, file]));
  const lines = String(diff || "").split("\n");
  const chunks = [];
  let current = null;

  function finishCurrent() {
    if (!current) return;
    current.diff = current.lines.join("\n");
    current.additions = current.lines.filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
    current.deletions = current.lines.filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
    const meta = fileMeta.get(current.path) || {};
    current.changeType = meta.change_type || inferChangeType(current.lines);
    chunks.push(current);
  }

  for (const line of lines) {
    const header = parseDiffHeader(line);
    if (header) {
      finishCurrent();
      current = {
        path: header.path,
        oldPath: header.oldPath,
        lines: [line],
        additions: 0,
        deletions: 0,
        changeType: "modified",
        diff: "",
      };
      continue;
    }

    if (!current && line.trim()) {
      current = {
        path: normalizedFiles[0]?.path || "unified.diff",
        oldPath: normalizedFiles[0]?.path || "unified.diff",
        lines: [],
        additions: 0,
        deletions: 0,
        changeType: normalizedFiles[0]?.change_type || "modified",
        diff: "",
      };
    }

    if (current) {
      current.lines.push(line);
      if (line.startsWith("+++ ")) {
        const path = stripDiffPathPrefix(line.slice(4).trim());
        if (path && path !== "/dev/null") current.path = path;
      }
    }
  }
  finishCurrent();

  const seen = new Set(chunks.map((chunk) => chunk.path));
  normalizedFiles.forEach((file) => {
    if (!seen.has(file.path)) {
      chunks.push({
        path: file.path,
        oldPath: file.path,
        additions: 0,
        deletions: 0,
        changeType: file.change_type,
        diff: "",
      });
    }
  });

  return chunks;
}

function inferChangeType(lines) {
  if (lines.some((line) => line.startsWith("new file mode"))) return "created";
  if (lines.some((line) => line.startsWith("deleted file mode"))) return "deleted";
  return "modified";
}

function setDiffState(diff, changedFiles = []) {
  state.diff = diff || "";
  state.diffFiles = parseUnifiedDiff(state.diff, changedFiles);
  if (!state.diffFiles.some((file) => file.path === state.selectedDiffFile)) {
    state.selectedDiffFile = state.diffFiles[0]?.path || "";
  }
}

function syncDiffFiles() {
  if (state.diffFiles?.length) {
    if (!state.selectedDiffFile || !state.diffFiles.some((file) => file.path === state.selectedDiffFile)) {
      state.selectedDiffFile = state.diffFiles[0]?.path || "";
    }
    return;
  }
  setDiffState(state.diff, state.report.changedFiles);
}

function resetRunView(prompt = "") {
  seenEventIds = new Set();
  state.messages = [];
  state.events = [];
  state.tasks = [];
  state.files = [];
  state.metrics = {
    tasks: 0,
    files: 0,
    toolCalls: 0,
    tokens: "--",
    tests: "--",
  };
  setDiffState("", []);
  state.previewUrl = "";
  state.report = blankReport();
  state.artifactCenter = blankArtifactCenter("loading");
  state.recoveryCenter = blankRecoveryCenter("loading");
  state.approval = blankApproval();
  state.runBlueprint = blankRunBlueprint();
  state.showCompletedTasks = false;
  if (prompt) {
    state.messages.push({
      role: "user",
      author: "用户",
      time: "",
      content: prompt,
    });
  }
}

function stopReplayTimer() {
  if (replayTimer) {
    window.clearTimeout(replayTimer);
    replayTimer = null;
  }
}

function setReplayEvents(events = [], { prompt = state.prompt, startedAt = "" } = {}) {
  state.replay.events = events;
  state.replay.index = events.length;
  state.replay.status = events.length ? "ready" : "idle";
  state.replay.prompt = prompt || "";
  state.replay.startedAt = startedAt || "";
}

function clearReplayState() {
  stopReplayTimer();
  state.replay = structuredClone(demoState.replay);
}

async function refreshReplayEvents(threadId, { prompt = state.prompt, startedAt = "" } = {}) {
  if (!threadId || threadId === "pending") return;
  try {
    const result = await fetchJson(`/api/runs/${encodeURIComponent(threadId)}/events/history`);
    setReplayEvents(result.events || [], { prompt, startedAt });
    render();
  } catch {
    // Replay is an enhancement; the live run view remains usable without it.
  }
}

function resetReplayToStart() {
  if (!state.replay.events.length) return;
  stopReplayTimer();
  state.replay.index = 0;
  state.replay.status = "paused";
  state.status = "replay_paused";
  state.activeTab = "timeline";
  resetRunView(state.replay.prompt || state.prompt);
  if (state.messages[0] && state.replay.startedAt) {
    state.messages[0].time = state.replay.startedAt;
  }
  render();
}

function setReplaySpeed(speed) {
  state.replay.speed = speed;
  if (state.replay.status === "playing") {
    stopReplayTimer();
    scheduleReplayStep();
  } else {
    render();
  }
}

function startReplay() {
  if (!state.replay.events.length) return;
  if (state.replay.index >= state.replay.events.length) {
    resetReplayToStart();
  }
  state.replay.status = "playing";
  state.status = "replaying";
  state.activeTab = "timeline";
  render();
  scheduleReplayStep();
}

function pauseReplay() {
  stopReplayTimer();
  if (state.replay.events.length) {
    state.replay.status = "paused";
    state.status = "replay_paused";
  }
  render();
}

function scheduleReplayStep() {
  stopReplayTimer();
  if (state.replay.status !== "playing") return;
  const delay = Math.max(120, 900 / Number(state.replay.speed || 1));
  replayTimer = window.setTimeout(() => {
    replayTimer = null;
    applyReplayStep();
  }, delay);
}

async function finishReplay() {
  stopReplayTimer();
  state.replay.status = "finished";
  state.replay.index = state.replay.events.length;
  if (state.status === "replaying") {
    state.status = "completed";
  }
  await hydrateRunArtifacts(state.currentThreadId, { refreshWorkspace: false });
  render();
}

function applyReplayStep() {
  if (state.replay.status !== "playing") return;
  const event = state.replay.events[state.replay.index];
  if (!event) {
    finishReplay();
    return;
  }

  handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false });
  state.replay.index += 1;

  if (state.replay.index >= state.replay.events.length) {
    finishReplay();
    return;
  }

  render();
  scheduleReplayStep();
}

async function requestJson(path, options = {}) {
  let lastError = null;

  for (const base of API_CANDIDATES) {
    try {
      const response = await fetch(`${base}${path}`, options);
      if (!response.ok) {
        throw new Error(`${path} HTTP ${response.status}`);
      }
      activeApiBase = base;
      return response.json();
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError || new Error(`${path} request failed`);
}

async function fetchJson(path) {
  return requestJson(path);
}

function normalizeApprovalTasks(tasks) {
  return Array.isArray(tasks)
    ? tasks.map((task) => ({
        id: task.id || task.title || String(task),
        title: task.title || task.id || String(task),
        status: task.status || "pending",
      }))
    : [];
}

async function submitApprovalDecision(decision) {
  if (!decision || state.approval?.status !== "pending") return;
  const comment = document.querySelector("#approval-comment")?.value.trim() || "";
  const planId = state.approval.planId || "default-plan";

  state.approval.status = "submitting";
  state.approval.decision = decision;
  state.approval.comment = comment;
  render();

  try {
    const event = await requestJson(`/api/runs/${encodeURIComponent(state.currentThreadId)}/approval`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, plan_id: planId, comment }),
    });
    handleAgentEvent(event);
    await refreshReplayEvents(state.currentThreadId);
  } catch (error) {
    handleAgentEvent({
      type: "approval_resolved",
      title: approvalDecisionLabel(decision),
      content: comment || `本地已记录审批结果：${approvalDecisionLabel(decision)}。`,
      agent: "user",
      payload: { plan_id: planId, decision, comment, local_only: true },
    });
    addTimelineEvent({
      type: "error",
      title: "审批结果未能写入后端",
      content: error.message,
    });
  }
}

function mapRunHistoryItem(run) {
  return {
    id: run.thread_id,
    kind: "run",
    title: runTitle(run.prompt, run.thread_id),
    status: run.status || "unknown",
    time: formatTime(run.updated_at || run.created_at),
    mode: run.mode || "agenthub_delivery",
    prompt: run.prompt || "",
    eventCount: run.event_count || 0,
    changedFilesCount: run.changed_files_count || 0,
    hasDiff: Boolean(run.has_diff),
    hasReport: Boolean(run.has_report),
    lastEventType: run.last_event_type || "",
  };
}

function mapConversationItem(conversation) {
  const conversationId = conversation.conversation_id || conversation.id;
  return {
    id: conversationId,
    kind: "conversation",
    conversationId,
    threadId: conversation.current_thread_id || "",
    title: conversation.title || runTitle(conversation.prompt, "新会话"),
    status: conversation.status || "draft",
    time: formatTime(conversation.updated_at || conversation.created_at),
    prompt: conversation.prompt || "",
    runIds: Array.isArray(conversation.run_ids) ? conversation.run_ids : [],
    agentCount: conversation.team_summary?.agent_count || conversation.team?.members?.length || 0,
  };
}

function conversationQuery() {
  return state.workspaceDir ? `?workspace_dir=${encodeURIComponent(state.workspaceDir)}` : "";
}

function teamToBackendMembers(team = state.team) {
  return team.map((member) => ({
    name: member.name,
    role: member.role,
    goal: member.goal || "",
    tools: Array.isArray(member.tools) ? member.tools : [],
    capabilities: Array.isArray(member.capabilities) ? member.capabilities : [],
    artifacts: Array.isArray(member.artifacts) ? member.artifacts : [],
  }));
}

function applyConversation(conversation, { reset = false } = {}) {
  if (!conversation?.conversation_id) return;
  state.currentConversationId = conversation.conversation_id;
  state.currentThreadId = conversation.current_thread_id || conversation.conversation_id;
  state.status = conversation.status === "draft" ? "idle" : conversation.status || "idle";
  state.prompt = conversation.prompt || state.prompt || "";
  if (conversation.team?.members?.length) {
    state.team = mapBackendTeam(conversation.team.members);
  }
  upsertRun(mapConversationItem(conversation));
  if (reset) {
    resetRunView("");
    state.messages = [
      {
        role: "assistant",
        author: "Lead Agent",
        time: nowTime(),
        content: state.workspaceDir
          ? "新会话已创建。输入需求后，我会为这次任务推荐 Agent 群组和执行蓝图。"
          : "新会话已创建。建议先打开一个项目目录，再开始交付任务。",
      },
    ];
  }
}

function upsertRun(run) {
  if (!run?.id) return;
  const index = state.runs.findIndex((item) => item.id === run.id);
  if (index >= 0) {
    state.runs[index] = { ...state.runs[index], ...run };
  } else {
    state.runs.unshift(run);
  }
}

async function loadRunHistory() {
  try {
    const [runsResult, conversationsResult] = await Promise.allSettled([
      fetchJson("/api/runs?limit=50"),
      fetchJson(`/api/conversations?limit=50${state.workspaceDir ? `&workspace_dir=${encodeURIComponent(state.workspaceDir)}` : ""}`),
    ]);
    const runs = runsResult.status === "fulfilled" ? (runsResult.value.runs || []).map(mapRunHistoryItem) : [];
    const conversations =
      conversationsResult.status === "fulfilled"
        ? (conversationsResult.value.conversations || []).map(mapConversationItem)
        : [];
    state.conversations = conversations;
    const runIdsLinkedToConversations = new Set(conversations.flatMap((item) => item.runIds || []));
    const standaloneRuns = runs.filter((run) => !runIdsLinkedToConversations.has(run.id));
    const transientRuns = state.runs.filter(
      (run) =>
        (run.localOnly || run.status === "running") &&
        !standaloneRuns.some((item) => item.id === run.id) &&
        !conversations.some((item) => item.id === run.id),
    );
    state.runs = [...transientRuns, ...conversations, ...standaloneRuns];
  } catch {
    // Keep the bundled demo sessions when the backend is not available.
  }
  render();
}

async function loadWorkspaceState() {
  try {
    const result = await fetchJson("/api/workspaces");
    const current = result.current_workspace || "";
    if (current) {
      state.workspaceDir = current;
      state.workspaceInput = current;
      localStorage.setItem("agenthub_workspace_dir", current);
    }
  } catch {
    // Keep the previous workspace path when the backend is not reachable.
  }
}

async function loadWorkspaceOverview() {
  try {
    const query = state.workspaceDir ? `?workspace_dir=${encodeURIComponent(state.workspaceDir)}` : "";
    const overview = await fetchJson(`/api/workspace/overview${query}`);
    state.projectOverview = {
      ...state.projectOverview,
      ...overview,
      summary: { ...(state.projectOverview?.summary || {}), ...(overview.summary || {}) },
      project_index: { ...(state.projectOverview?.project_index || {}), ...(overview.project_index || {}) },
      recovery: { ...(state.projectOverview?.recovery || {}), ...(overview.recovery || {}) },
    };
  } catch {
    state.projectOverview = {
      ...state.projectOverview,
      workspace_dir: state.workspaceDir || state.projectOverview?.workspace_dir || "",
    };
  }
}

async function openWorkspace() {
  const dir = (state.workspaceInput || "").trim();
  if (!dir) {
    addTimelineEvent({
      type: "error",
      title: "工作区路径为空",
      content: "请输入项目目录的绝对路径。",
    });
    return;
  }

  try {
    const result = await requestJson("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dir }),
    });
    state.workspaceDir = result.workspace_dir || dir;
    state.workspaceInput = state.workspaceDir;
    localStorage.setItem("agenthub_workspace_dir", state.workspaceDir);
    await startNewSession({ announce: false });
    addTimelineEvent({
      type: "workspace_opened",
      title: "项目目录已打开",
      content: state.workspaceDir,
    });
    await Promise.allSettled([
      loadWorkspaceOverview(),
      loadRunHistory(),
      loadCapabilities(),
      loadBenchmarks(),
      loadMemoryProfile(),
      loadRecoveryCenter(),
      refreshWorkspaceData({ allowEmpty: true }),
    ]);
    render();
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "打开项目目录失败",
      content: error.message,
    });
    render();
  }
}

async function startNewSession({ draftId = "", keepRunItem = false, announce = true } = {}) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  stopReplayTimer();
  clearReplayState();

  let id = draftId || `draft-${Date.now().toString(36)}`;
  let conversation = null;
  if (!draftId) {
    try {
      const result = await requestJson("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: "", workspace_dir: state.workspaceDir || undefined }),
      });
      conversation = result.conversation;
      id = conversation?.conversation_id || id;
    } catch (error) {
      addTimelineEvent({
        type: "error",
        title: "会话后端暂不可用",
        content: `已创建本地草稿：${error.message}`,
      });
    }
  }
  state.currentConversationId = conversation?.conversation_id || (id.startsWith("conv-") ? id : "");
  state.currentThreadId = id;
  state.status = "idle";
  state.prompt = "";
  state.activeTab = "timeline";
  state.runBlueprint = blankRunBlueprint();
  resetRunView("");
  state.messages = [
    {
      role: "assistant",
      author: "Lead Agent",
      time: nowTime(),
      content: state.workspaceDir
        ? "新会话已创建。输入需求后，我会在当前项目目录下生成执行蓝图。"
        : "新会话已创建。建议先打开一个项目目录，再开始交付任务。",
    },
  ];

  if (conversation) {
    applyConversation(conversation, { reset: false });
  }

  if (!keepRunItem) {
    state.runs = state.runs.filter((run) => !run.localOnly);
    upsertRun({
      id,
      kind: conversation ? "conversation" : "run",
      conversationId: conversation?.conversation_id || "",
      title: conversation?.title || "新会话",
      status: "idle",
      time: nowTime(),
      prompt: "",
      localOnly: !conversation,
      agentCount: conversation?.team?.members?.length || state.team.length,
    });
  }

  if (announce) {
    render();
    refreshWorkspaceData({ allowEmpty: true }).catch(() => render());
  }
}

async function restoreRun(threadId) {
  if (!threadId) return;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  stopReplayTimer();

  const selectedRun = state.runs.find((run) => run.id === threadId);
  if (selectedRun?.kind === "conversation") {
    await restoreConversation(selectedRun.conversationId || selectedRun.id);
    return;
  }
  if (threadId === state.currentThreadId) return;
  if (selectedRun?.localOnly) {
    await startNewSession({ draftId: selectedRun.id, keepRunItem: true });
    return;
  }
  state.currentThreadId = threadId;
  state.status = selectedRun?.status || "running";
  state.prompt = selectedRun?.prompt || "";
  state.activeTab = "timeline";
  resetRunView(state.prompt);
  addTimelineEvent({
    type: "run_restoring",
    title: "正在恢复历史运行",
    content: selectedRun?.title || threadId,
  });

  try {
    const [sessionResult, eventsResult] = await Promise.allSettled([
      fetchJson(`/api/runs/${encodeURIComponent(threadId)}`),
      fetchJson(`/api/runs/${encodeURIComponent(threadId)}/events/history`),
    ]);

    const session = sessionResult.status === "fulfilled" ? sessionResult.value : null;
    const prompt = session?.prompt || selectedRun?.prompt || "";
    if (session?.conversation_id) {
      state.currentConversationId = session.conversation_id;
    }
    if (Array.isArray(session?.team) && session.team.length) {
      state.team = mapBackendTeam(session.team);
    }
    state.prompt = prompt || state.prompt;
    state.status = session?.status || selectedRun?.status || state.status;

    resetRunView(state.prompt);
    if (session?.execution_plan) {
      syncTasksFromExecutionPlan(session.execution_plan);
      state.rightTab = state.tasks.length ? "tasks" : state.rightTab;
    }
    if (state.prompt) {
      state.messages[0].time = formatTime(session?.created_at) || selectedRun?.time || "";
    }

    upsertRun({
      id: threadId,
      title: runTitle(state.prompt, threadId),
      status: state.status,
      time: formatTime(session?.updated_at || session?.created_at) || selectedRun?.time || "",
      mode: session?.mode || selectedRun?.mode || "agenthub_delivery",
      prompt: state.prompt,
      eventCount: selectedRun?.eventCount || 0,
      changedFilesCount: selectedRun?.changedFilesCount || 0,
      hasDiff: selectedRun?.hasDiff || false,
      hasReport: selectedRun?.hasReport || false,
      lastEventType: selectedRun?.lastEventType || "",
    });

    if (eventsResult.status === "fulfilled") {
      const events = eventsResult.value.events || [];
      setReplayEvents(events, {
        prompt: state.prompt,
        startedAt: formatTime(session?.created_at) || selectedRun?.time || "",
      });
      events.forEach((event) => {
        handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false });
      });
      state.replay.index = events.length;
      state.replay.status = events.length ? "ready" : "idle";
      const currentRun = state.runs.find((run) => run.id === threadId);
      if (currentRun) {
        currentRun.eventCount = events.length;
        currentRun.lastEventType = events.at(-1)?.type || currentRun.lastEventType;
      }
    } else {
      state.events.push({
        type: "error",
        title: "历史事件读取失败",
        content: eventsResult.reason?.message || "后端未返回事件列表。",
        time: nowTime(),
      });
    }

    await hydrateRunArtifacts(threadId, { refreshWorkspace: false });
    if (state.report.markdown) {
      state.activeTab = "report";
    } else if (state.diff) {
      state.activeTab = "diff";
    }
  } catch (error) {
    state.status = "failed";
    updateCurrentRunStatus("failed");
    state.events.push({
      type: "error",
      title: "恢复历史运行失败",
      content: error.message,
      time: nowTime(),
    });
  }

  render();
}

async function restoreConversation(conversationId) {
  if (!conversationId || conversationId === state.currentConversationId) return;
  state.activeTab = "timeline";
  resetRunView("");
  addTimelineEvent({
    type: "conversation_restoring",
    title: "正在恢复会话",
    content: conversationId,
  });

  try {
    const result = await fetchJson(`/api/conversations/${encodeURIComponent(conversationId)}${conversationQuery()}`);
    const conversation = result.conversation;
    applyConversation(conversation, { reset: false });
    state.messages = [
      {
        role: "assistant",
        author: "Lead Agent",
        time: formatTime(conversation.updated_at) || nowTime(),
        content: conversation.current_thread_id
          ? "会话已恢复。可以查看上次运行，也可以输入新需求继续让 nanoCursor 组队执行。"
          : "会话已恢复。输入需求后，我会基于这个会话团队生成执行蓝图。",
      },
    ];
    if (conversation.current_thread_id) {
      await restoreRun(conversation.current_thread_id);
      state.currentConversationId = conversationId;
    }
  } catch (error) {
    state.status = "failed";
    addTimelineEvent({
      type: "error",
      title: "恢复会话失败",
      content: error.message,
    });
  }

  render();
}

async function createCustomAgent() {
  const name = document.querySelector("#agent-name")?.value.trim();
  const role = document.querySelector("#agent-role")?.value.trim();
  const goal = document.querySelector("#agent-goal")?.value.trim() || "";
  const tools = (document.querySelector("#agent-tools")?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const capabilities = Array.from(document.querySelectorAll("input[name='agent-capability']:checked"))
    .map((item) => item.value)
    .filter(Boolean);

  if (!name || !role) {
    addTimelineEvent({
      type: "error",
      title: "Agent 信息不完整",
      content: "请填写 Agent 名称和角色。",
    });
    return;
  }

  try {
    if (state.currentConversationId) {
      const nextTeam = [
        ...teamToBackendMembers(),
        {
          name,
          role,
          goal,
          tools,
          capabilities,
        },
      ];
      await saveConversationTeam(nextTeam);
      state.rightTab = "team";
      addTimelineEvent({
        type: "team_updated",
        title: "会话 Agent 已添加",
        content: `${name} 将以 ${role} 角色参与当前会话，已装配 ${capabilities.length} 个能力包。`,
      });
      return;
    }

    const result = await requestJson("/api/team/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, role, goal, tools, capabilities }),
    });
    state.team = mapBackendTeam(result.members || []);
    state.rightTab = "team";
    addTimelineEvent({
      type: "team_updated",
      title: "自定义 Agent 已添加",
      content: `${name} 将以 ${role} 角色参与后续交付，已装配 ${capabilities.length} 个能力包。`,
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "添加 Agent 失败",
      content: error.message,
    });
  }
}

async function saveConversationTeam(members) {
  await ensureConversation(state.prompt);
  const result = await requestJson(`/api/conversations/${encodeURIComponent(state.currentConversationId)}/team`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ members, workspace_dir: state.workspaceDir || undefined }),
  });
  state.team = mapBackendTeam(result.team?.members || members);
  upsertRun({
    id: state.currentConversationId,
    kind: "conversation",
    conversationId: state.currentConversationId,
    title: runTitle(state.prompt, "新会话"),
    status: state.status === "running" ? "running" : "idle",
    time: nowTime(),
    prompt: state.prompt,
    agentCount: state.team.length,
  });
}

async function removeTeamMember(index) {
  if (index < 0 || index >= state.team.length || state.team.length <= 1) return;
  const removed = state.team[index];
  const members = teamToBackendMembers(state.team.filter((_, itemIndex) => itemIndex !== index));
  try {
    await saveConversationTeam(members);
    addTimelineEvent({
      type: "team_updated",
      title: "会话 Agent 已移除",
      content: `${removed.name} 已从当前会话团队中移除。`,
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "移除 Agent 失败",
      content: error.message,
    });
  }
}

async function loadBenchmarks() {
  try {
    const result = await fetchJson("/api/benchmarks");
    if (Array.isArray(result.benchmarks) && result.benchmarks.length) {
      state.benchmarks = result.benchmarks;
      render();
    }
  } catch {
    render();
  }
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
    const result = await requestJson("/api/capabilities/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content, description: content.slice(0, 180) }),
    });
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
  try {
    const result = await fetchJson("/api/capabilities");
    if (Array.isArray(result.groups)) {
      state.capabilityHub = result;
      state.capabilityRecommendation = inferLocalCapabilityRecommendation(state.prompt);
      render();
    }
  } catch {
    state.capabilityRecommendation = inferLocalCapabilityRecommendation(state.prompt);
    render();
  }
}

function scheduleCapabilityRecommendation(prompt) {
  if (state.capabilityRecommendationMuted) return;
  window.clearTimeout(recommendationTimer);
  recommendationTimer = window.setTimeout(() => {
    refreshCapabilityRecommendation(prompt);
  }, 350);
}

async function refreshCapabilityRecommendation(prompt) {
  const text = String(prompt || "").trim();
  if (!text) return;
  if (text !== String(state.prompt || "").trim()) return;
  try {
    const result = await requestJson("/api/capabilities/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    if (text !== String(state.prompt || "").trim()) return;
    state.capabilityRecommendation = normalizeCapabilityRecommendation(result);
  } catch {
    if (text !== String(state.prompt || "").trim()) return;
    state.capabilityRecommendation = inferLocalCapabilityRecommendation(text);
  }
  if (document.activeElement?.id === "prompt-input") {
    recommendationRenderDeferred = true;
    return;
  }
  render();
}

function normalizeCapabilityRecommendation(result) {
  const capabilities = Array.isArray(result.capabilities) ? result.capabilities : [];
  return {
    agents: Array.isArray(result.agents) ? result.agents : [],
    capabilities,
    reasons: Array.isArray(result.reasons) ? result.reasons : [],
    summary: result.summary || {
      agent_count: Array.isArray(result.agents) ? result.agents.length : 0,
      capability_count: capabilities.length,
      ready_count: capabilities.filter((item) => item.status === "ready" || item.status === "configured").length,
      planned_count: capabilities.filter((item) => item.status === "planned").length,
    },
  };
}

function inferLocalCapabilityRecommendation(prompt) {
  const text = String(prompt || "").toLowerCase();
  const rules = [
    {
      keywords: ["前端", "界面", "页面", "ui", "样式", "好看", "美化", "布局", "交互", "响应式"],
      agents: ["Designer", "Coder", "Reviewer"],
      capabilityIds: ["skill.frontend-polish", "tool.file_ops", "tool.project_index", "mcp.figma"],
      reason: "需求涉及界面和交互体验，适合启用前端打磨 Skill，并让 Designer 与 Coder 协同。",
    },
    {
      keywords: ["测试", "验证", "质量", "复核", "review", "bug", "修复", "报错", "异常", "回归"],
      agents: ["Tester", "Reviewer", "Coder"],
      capabilityIds: ["skill.delivery-review", "tool.project_index", "tool.recovery"],
      reason: "需求涉及质量或缺陷修复，需要测试、复核和可恢复保障。",
    },
    {
      keywords: ["github", "issue", "pr", "pull request", "ci", "仓库", "代码审查"],
      agents: ["Lead", "Reviewer"],
      capabilityIds: ["mcp.github", "skill.delivery-review"],
      reason: "需求涉及研发协作平台，后续可接 GitHub MCP 查看 Issue、PR 和 CI。",
    },
    {
      keywords: ["文档", "readme", "接口", "api", "知识库", "说明", "规范", "需求"],
      agents: ["Planner", "Tester"],
      capabilityIds: ["mcp.docs", "tool.project_index", "skill.delivery-review"],
      reason: "需求涉及文档和规范，需要 Planner 做结构化理解，并用知识库能力补充上下文。",
    },
    {
      keywords: ["偏好", "记住", "风格", "习惯", "长期", "记忆"],
      agents: ["Lead", "Planner"],
      capabilityIds: ["tool.memory", "skill.frontend-polish"],
      reason: "需求涉及个人偏好或长期记忆，适合启用偏好记忆能力。",
    },
  ];
  const matched = rules.filter((rule) => rule.keywords.some((keyword) => text.includes(keyword)));
  const activeRules = matched.length
    ? matched
    : [
        {
          agents: ["Lead", "Planner", "Coder", "Tester"],
          capabilityIds: ["tool.project_index", "tool.file_ops", "skill.delivery-review"],
          reason: "默认按完整软件交付流程推荐：先理解项目，再实现变更，最后复核质量。",
        },
      ];
  const agents = uniqueItems(activeRules.flatMap((rule) => rule.agents));
  const capabilities = uniqueItems(activeRules.flatMap((rule) => rule.capabilityIds)).map(resolveCapabilityById);
  return normalizeCapabilityRecommendation({
    agents,
    capabilities,
    reasons: activeRules.map((rule) => rule.reason).slice(0, 3),
  });
}

function resolveCapabilityById(capabilityId) {
  const capability = (state.capabilityHub?.capabilities || getCapabilityOptions()).find((item) => item.id === capabilityId);
  return (
    capability || {
      id: capabilityId,
      name: capabilityDisplayName(capabilityId),
      kind: capabilityId.split(".", 1)[0],
      status: "planned",
      description: "推荐的扩展能力，当前尚未配置。",
      tags: [],
      agents: [],
    }
  );
}

function uniqueItems(items) {
  return [...new Set(items.filter(Boolean))];
}

async function prepareRunBlueprint(prompt) {
  const text = String(prompt || "").trim();
  if (!text) return;
  state.prompt = text;
  state.runBlueprint = {
    ...blankRunBlueprint(),
    status: "loading",
    prompt: text,
    title: "nanoCursor 执行蓝图",
  };
  render();

  try {
    await ensureConversation(text);
    await refreshConversationTeam(text, { renderAfter: false });
    const result = await requestJson("/api/runs/blueprint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    state.runBlueprint = normalizeRunBlueprint(result);
    if (state.team.length) {
      state.runBlueprint.agents = state.team.map((member) => member.name);
      state.runBlueprint.summary.agent_count = state.team.length;
    }
  } catch {
    state.runBlueprint = inferLocalRunBlueprint(text);
  }
  render();
}

async function ensureConversation(prompt = state.prompt) {
  if (state.currentConversationId) return state.currentConversationId;
  const result = await requestJson("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, workspace_dir: state.workspaceDir || undefined }),
  });
  applyConversation(result.conversation || {}, { reset: false });
  return state.currentConversationId;
}

async function refreshConversationTeam(prompt = state.prompt, { renderAfter = true } = {}) {
  const text = String(prompt || "").trim();
  if (!text || !state.currentConversationId) return;
  const result = await requestJson(`/api/conversations/${encodeURIComponent(state.currentConversationId)}/team/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: text, workspace_dir: state.workspaceDir || undefined }),
  });
  const team = result.team || {};
  const recommendation = result.recommendation || team.recommendation || {};
  if (Array.isArray(team.members)) {
    state.team = mapBackendTeam(team.members);
  }
  state.capabilityRecommendation = normalizeCapabilityRecommendation({
    agents: state.team.map((member) => member.name),
    capabilities: recommendation.capabilities || [],
    reasons: recommendation.reasons || [],
    summary: recommendation.summary,
  });
  upsertRun({
    id: state.currentConversationId,
    kind: "conversation",
    conversationId: state.currentConversationId,
    title: runTitle(text, "新会话"),
    status: "idle",
    time: nowTime(),
    prompt: text,
    agentCount: state.team.length,
  });
  if (renderAfter) render();
}

function normalizeRunBlueprint(result) {
  const stages = Array.isArray(result.stages) ? result.stages : [];
  const capabilities = Array.isArray(result.capabilities) ? result.capabilities : [];
  const risks = Array.isArray(result.risks) ? result.risks : [];
  return {
    status: "ready",
    prompt: result.prompt || state.prompt,
    title: result.title || "nanoCursor 执行蓝图",
    agents: Array.isArray(result.agents) ? result.agents : [],
    capabilities,
    stages,
    risks,
    reasons: Array.isArray(result.reasons) ? result.reasons : [],
    summary: result.summary || {
      stage_count: stages.length,
      agent_count: Array.isArray(result.agents) ? result.agents.length : 0,
      capability_count: capabilities.length,
      risk_count: risks.length,
    },
  };
}

function inferLocalRunBlueprint(prompt) {
  const recommendation = inferLocalCapabilityRecommendation(prompt);
  const text = String(prompt || "").toLowerCase();
  const stages = [
    {
      id: "understand",
      title: "理解需求与项目上下文",
      owner: "Planner",
      description: "识别验收点，并结合项目索引判断影响范围。",
    },
    {
      id: "plan",
      title: "生成执行计划",
      owner: "Lead",
      description: "确认任务阶段、负责人、能力包和风险控制点。",
    },
    {
      id: "implement",
      title: "实现代码变更",
      owner: "Coder",
      description: "按计划修改文件，并保持 Diff 可审查。",
    },
    {
      id: "verify",
      title: "验证与复核",
      owner: "Tester",
      description: "检查需求覆盖、测试结果、恢复点和交付风险。",
    },
  ];
  if (["前端", "界面", "页面", "ui", "样式", "交互"].some((keyword) => text.includes(keyword))) {
    stages.push({
      id: "design_review",
      title: "体验与界面复核",
      owner: "Designer",
      description: "检查信息层级、视觉密度和交互连续性。",
    });
  }
  const risks = [
    recommendation.summary?.planned_count
      ? {
          level: "medium",
          title: "存在待接入能力",
          detail: "部分 MCP 能力当前是规划状态，会先以本地工具兜底。",
        }
      : {
          level: "low",
          title: "常规交付风险",
          detail: "重点关注 Diff 审查、测试验证和需求覆盖。",
        },
  ];
  return normalizeRunBlueprint({
    prompt,
    title: "nanoCursor 执行蓝图",
    agents: recommendation.agents,
    capabilities: recommendation.capabilities,
    stages,
    risks,
    reasons: recommendation.reasons,
  });
}

async function runBenchmark(benchmarkId) {
  const benchmark = state.benchmarks.find((item) => item.id === benchmarkId);
  if (!benchmark) return;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  clearReplayState();

  state.status = "running";
  state.currentThreadId = "pending";
  state.activeTab = "artifacts";
  resetRunView(`运行基准任务：${benchmark.title}`);
  addMessage({
    role: "assistant",
    author: "Lead Agent",
    content: "我会按固定验收标准执行 Benchmark，并归档评分、测试、Diff 和交付物。",
  });
  render();

  try {
    const run = await requestJson("/api/benchmarks/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ benchmark_id: benchmarkId, workspace_dir: state.workspaceDir || undefined }),
    });
    state.currentThreadId = run.thread_id;
    state.runs.unshift({
      id: run.thread_id,
      title: `Benchmark: ${run.title}`,
      status: "running",
      time: nowTime(),
    });
    addTimelineEvent({
      type: "run_started",
      title: "Benchmark 已启动",
      content: `${run.title} · ${run.thread_id}`,
    });
    await refreshWorkspaceData({ allowEmpty: false });
    connectEvents(run.thread_id);
  } catch (error) {
    state.status = "failed";
    addTimelineEvent({
      type: "error",
      title: "Benchmark 启动失败",
      content: error.message,
    });
  }
}

async function loadMemoryProfile() {
  try {
    const profile = await fetchJson("/api/preferences/profile");
    state.memoryProfile = profile;
    render();
  } catch {
    render();
  }
}

async function loadRecoveryCenter() {
  try {
    state.recoveryCenter = await fetchJson("/api/recovery");
    render();
  } catch {
    render();
  }
}

async function createPreference() {
  const preferenceType = document.querySelector("#preference-type")?.value;
  const content = document.querySelector("#preference-content")?.value.trim();
  if (!preferenceType || !content) {
    addTimelineEvent({
      type: "error",
      title: "偏好内容为空",
      content: "请先填写一条希望 nanoCursor 记住的偏好。",
    });
    return;
  }

  try {
    const result = await requestJson("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preference_type: preferenceType,
        content,
        importance: 8,
      }),
    });
    state.memoryProfile = result.profile;
    state.rightTab = "preferences";
    addTimelineEvent({
      type: "memory_updated",
      title: "偏好已保存",
      content,
    });
  } catch (error) {
    addTimelineEvent({
      type: "error",
      title: "保存偏好失败",
      content: error.message,
    });
  }
}

function fileType(path, isDir = false) {
  if (isDir) return "dir";
  const ext = path.split(".").pop();
  return ext && ext !== path ? ext : "txt";
}

function mapBackendTasks(tasks) {
  return tasks.map(normalizeTask).filter(Boolean);
}

function tasksFromExecutionPlan(executionPlan) {
  const tasks = Array.isArray(executionPlan?.tasks) ? executionPlan.tasks : [];
  const stages = Array.isArray(executionPlan?.stages) ? executionPlan.stages : [];
  const stageById = new Map(stages.map((stage) => [stage.id, stage]));
  return tasks
    .map((task) => {
      const stageId = stageIdFromTaskId(task.id);
      const stage = stageById.get(stageId) || {};
      return normalizeTask({
        ...task,
        title: task.title || stage.title,
        description: task.description || stage.description,
        status: stage.status || task.status,
        owner: task.owner || stage.owner,
        capabilities: task.capabilities?.length ? task.capabilities : stage.capabilities,
        tool_evidence: task.tool_evidence || stage.tool_evidence,
        failure: task.failure || stage.failure,
        source: "execution_plan",
      });
    })
    .filter(Boolean);
}

function syncTasksFromExecutionPlan(executionPlan) {
  tasksFromExecutionPlan(executionPlan).forEach((task) => upsertTask(task));
}

function stageIdFromTaskId(taskId = "") {
  const match = String(taskId).match(/^stage-\d+-(.+)$/);
  return match ? match[1] : "";
}

function taskForStageId(stageId) {
  if (!stageId) return null;
  return state.tasks.find((task) => task.id === stageId || String(task.id || "").endsWith(`-${stageId}`));
}

function mapBackendTeam(members) {
  const initialsByRole = {
    lead: "L",
    planner: "P",
    coder: "C",
    tester: "T",
    reviewer: "R",
    designer: "D",
    devops: "O",
  };
  return members.map((member) => {
    const role = String(member.role || "agent").toLowerCase();
    const tone = agentToneFromName(`${member.name || ""} ${role}`, "lead");
    return {
      name: member.name || role,
      role: member.role || "agent",
      status: member.status || "idle",
      initials: initialsByRole[role] || String(member.name || "A").slice(0, 1).toUpperCase(),
      tone,
      goal: member.goal || "",
      tools: Array.isArray(member.tools) ? member.tools : [],
      capabilities: Array.isArray(member.capabilities) ? member.capabilities : [],
      lastAction: member.last_action || member.lastAction || "",
      artifacts: Array.isArray(member.artifacts) ? member.artifacts : [],
      source: member.source || "workspace",
    };
  });
}

function upsertTask(task) {
  if (!task?.id) return;
  const existing = state.tasks.find((item) => item.id === task.id);
  const normalized = normalizeTask(task);
  if (!normalized) return;

  if (existing) {
    Object.assign(existing, normalized);
  } else {
    state.tasks.push(normalized);
  }
  state.metrics.tasks = state.tasks.length;
}

function patchTask(taskId, patch) {
  if (!taskId) return;
  const task = state.tasks.find((item) => item.id === taskId);
  if (task) {
    const normalized = normalizeTask({ ...task, ...patch, id: taskId });
    if (normalized) Object.assign(task, normalized);
    return;
  }
  const normalized = normalizeTask({ ...patch, id: taskId });
  if (!normalized) return;
  state.tasks.push(normalized);
  state.metrics.tasks = state.tasks.length;
}

function patchStageTask(stageUpdate = {}) {
  const stageId = stageUpdate.stage_id || stageUpdate.stageId || "";
  if (!stageId) return;
  const existing = taskForStageId(stageId);
  const taskId = existing?.id || `stage-${String(state.tasks.length + 1).padStart(2, "0")}-${stageId}`;
  patchTask(taskId, {
    title: stageUpdate.title || existing?.title || stageId,
    description: stageUpdate.description || existing?.description || "",
    status: stageUpdate.status || existing?.status || "pending",
    owner: stageUpdate.owner || existing?.owner || "Agent",
    failure: stageUpdate.failure || existing?.failure || "",
  });
}

function attachToolEvidenceToTask(stageId, evidence) {
  const task = taskForStageId(stageId);
  if (!task) return;
  const toolEvidence = Array.isArray(task.toolEvidence) ? task.toolEvidence : [];
  task.toolEvidence = [...toolEvidence, evidence].slice(-12);
}

function normalizeTask(task) {
  if (!task?.id) return null;
  const title = String(task.title || task.subject || "").trim();
  const description = String(task.description || "").trim();
  if (!title && !description) return null;
  const normalized = {
    id: task.id,
    title,
    description,
    status: task.status || "pending",
    owner: task.owner || "Agent",
    capabilities: Array.isArray(task.capabilities) ? task.capabilities : [],
    toolEvidence: Array.isArray(task.toolEvidence)
      ? task.toolEvidence
      : Array.isArray(task.tool_evidence)
        ? task.tool_evidence
        : [],
    failure: task.failure || "",
    source: task.source || "",
  };
  normalized.capabilities = normalized.capabilities.length ? normalized.capabilities : inferTaskCapabilities(normalized);
  return normalized;
}

function upsertFile(file) {
  const path = typeof file === "string" ? file : file?.path;
  if (!path) return;
  state.files = state.files.map((item) => ({ ...item, active: false }));
  const existing = state.files.find((item) => item.path === path);
  if (existing) {
    existing.active = true;
    existing.type = fileType(path);
  } else {
    state.files.unshift({
      path,
      type: fileType(path),
      active: true,
    });
  }
  state.metrics.files = state.files.length;
}

async function refreshWorkspaceData({ allowEmpty = false, announce = false } = {}) {
  const results = await Promise.allSettled([
    fetchJson("/api/files"),
    fetchJson("/api/tasks"),
    fetchJson("/api/team"),
  ]);

  const [filesResult, tasksResult, teamResult] = results;

  if (filesResult.status === "fulfilled") {
    const files = filesResult.value.files || [];
    if (files.length || allowEmpty) {
      state.files = files.slice(0, 80).map((file, index) => ({
        path: file.path,
        type: fileType(file.path, file.is_dir),
        active: index === 0,
      }));
      state.metrics.files = files.filter((file) => !file.is_dir).length;
    }
  }

  if (tasksResult.status === "fulfilled") {
    const tasks = tasksResult.value.tasks || [];
    if (tasks.length || allowEmpty) {
      state.tasks = mapBackendTasks(tasks);
      state.metrics.tasks = tasks.length;
    }
  }

  if (teamResult.status === "fulfilled") {
    const members = teamResult.value.members || [];
    if (members.length || allowEmpty) {
      state.team = mapBackendTeam(members);
    }
  }

  if (announce) {
    addTimelineEvent({
      type: "metrics_updated",
      title: "工作区数据已同步",
      content: "文件、任务和团队状态已从后端刷新。",
    });
  } else {
    render();
  }
}

async function hydrateRunArtifacts(threadId, { refreshWorkspace = true } = {}) {
  const requests = [
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/diff`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/report`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/traceability`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/artifacts`),
    fetchJson(`/api/runs/${encodeURIComponent(threadId)}/recovery`),
  ];
  if (refreshWorkspace) {
    requests.push(refreshWorkspaceData({ allowEmpty: true }));
  }

  const results = await Promise.allSettled(requests);

  const [diffResult, reportResult, traceabilityResult, artifactsResult, recoveryResult] = results;

  if (diffResult.status === "fulfilled") {
    const diffInfo = diffResult.value;
    const diffText =
      diffInfo.diff ||
      `No diff detected for ${threadId}.\n\nChanged files: ${(diffInfo.changed_files || [])
        .map((file) => file.path)
        .join(", ") || "none"}`;
    setDiffState(diffText, diffInfo.changed_files || []);
    if (Array.isArray(diffInfo.changed_files) && diffInfo.changed_files.length) {
      state.report.changedFiles = diffInfo.changed_files.map((file) => file.path);
      state.metrics.files = diffInfo.changed_files.length;
    }
  }

  if (reportResult.status === "fulfilled") {
    const report = reportResult.value;
    state.report.summary = report.summary || state.report.summary;
    state.report.markdown = report.markdown || "";
    state.report.changedFiles = (report.changed_files || state.report.changedFiles).map((item) =>
      typeof item === "string" ? item : item.path,
    );
    state.report.risks = report.risks?.length ? report.risks : state.report.risks;
  }

  if (traceabilityResult.status === "fulfilled") {
    setTraceability(traceabilityResult.value);
  }

  if (artifactsResult.status === "fulfilled") {
    state.artifactCenter = artifactsResult.value;
  }

  if (recoveryResult.status === "fulfilled") {
    state.recoveryCenter = recoveryResult.value;
  }

  render();
}

async function runPrompt(prompt, options = {}) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  clearReplayState();

  state.status = "running";
  state.prompt = prompt;
  state.currentThreadId = "pending";
  resetRunView(prompt);
  addMessage({
    role: "assistant",
    author: "Lead Agent",
    content: "我正在连接后端 Agent Runtime，并准备接收实时事件。",
  });
  render();

  try {
    if (!options.demo) {
      await ensureConversation(prompt);
      await saveConversationTeam(teamToBackendMembers());
    }
    state.runs = state.runs.filter((runItem) => !runItem.localOnly);
    const endpoint = options.demo
      ? "/api/runs/demo"
      : `/api/conversations/${encodeURIComponent(state.currentConversationId)}/runs`;
    const result = await requestJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, workspace_dir: state.workspaceDir || undefined }),
    });
    const run = result.run || result;
    if (result.conversation) {
      applyConversation(result.conversation, { reset: false });
    }
    state.currentThreadId = run.thread_id;
    upsertRun({
      id: run.thread_id,
      kind: "run",
      title: runTitle(prompt, "新任务"),
      status: "running",
      time: nowTime(),
      prompt,
    });
    if (state.currentConversationId) {
      upsertRun({
        id: state.currentConversationId,
        kind: "conversation",
        conversationId: state.currentConversationId,
        threadId: run.thread_id,
        title: runTitle(prompt, "新会话"),
        status: "running",
        time: nowTime(),
        prompt,
        agentCount: state.team.length,
      });
    }
    addTimelineEvent({
      type: "run_started",
      title: "后端运行已启动",
      content: `Thread: ${run.thread_id}`,
    });
    await refreshWorkspaceData({ allowEmpty: false });
    connectEvents(run.thread_id);
  } catch (error) {
    state.status = "failed";
    addMessage({
      role: "assistant",
      author: "Lead Agent",
      content: `后端暂时不可用：${error.message}。当前页面仍可使用 Demo 数据展示工作台形态。`,
    });
    addTimelineEvent({
      type: "error",
      title: "连接失败",
      content: `${API_CANDIDATES.join(" 或 ")} 未返回可用响应。`,
    });
  }
}

function connectEvents(threadId) {
  eventSource = new EventSource(`${activeApiBase}/api/runs/${encodeURIComponent(threadId)}/events`);

  eventSource.onmessage = (event) => {
    if (event.data?.trim()) {
      handleAgentEvent(JSON.parse(event.data));
    }
  };

  [
    "run_started",
    "assistant_message",
    "plan_created",
    "approval_requested",
    "approval_resolved",
    "stage_updated",
    "task_created",
    "task_updated",
    "team_updated",
    "tool_call_finished",
    "file_changed",
    "diff_updated",
    "test_finished",
    "preview_started",
    "report_ready",
    "traceability_ready",
    "benchmark_finished",
    "metrics_updated",
    "done",
    "error",
  ].forEach((type) => {
    eventSource.addEventListener(type, (event) => {
      handleAgentEvent(JSON.parse(event.data));
    });
  });

  eventSource.onerror = () => {
    eventSource?.close();
    if (state.status !== "running") return;

    fetchJson(`/api/runs/${encodeURIComponent(threadId)}`)
      .then(async (session) => {
        const status = session.status || "running";
        if (["completed", "failed", "cancelled"].includes(status)) {
          state.status = status;
          updateCurrentRunStatus(status);
          await hydrateRunArtifacts(threadId);
          return;
        }
        addTimelineEvent({
          type: "metrics_updated",
          title: "事件流已断开",
          content: "后端运行记录仍存在，可通过同步或历史运行恢复状态。",
        });
      })
      .catch((error) => {
        addTimelineEvent({
          type: "error",
          title: "事件流连接失败",
          content: `无法确认 run 状态：${error.message}`,
        });
        state.status = "failed";
        updateCurrentRunStatus("failed");
        render();
      });
  };
}

function handleAgentEvent(event, options = {}) {
  if (event.id) {
    if (seenEventIds.has(event.id)) return;
    seenEventIds.add(event.id);
  }
  const eventType = event.type || "message";
  const title = event.title || eventType;
  const content = event.content || "";
  const time = event.timestamp
    ? new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(event.timestamp * 1000))
    : nowTime();

  state.events.push({
    type: eventType,
    title,
    content,
    time,
    agent: event.agent || "",
    payload: event.payload || {},
  });

  if (eventType === "assistant_message") {
    addMessage({
      role: "assistant",
      author: `${event.agent || "Lead"} Agent`,
      content,
      time,
    });
  }

  if (eventType === "plan_created" && event.payload?.tasks) {
    event.payload.tasks.forEach((task) => upsertTask(task));
    state.rightTab = "tasks";
  }

  if (eventType === "stage_updated" && event.payload?.stage_id) {
    patchStageTask(event.payload);
    state.rightTab = "tasks";
  }

  if (eventType === "approval_requested") {
    state.approval = {
      status: "pending",
      planId: event.payload?.plan_id || "default-plan",
      title,
      content,
      riskLevel: event.payload?.risk_level || "",
      tasks: normalizeApprovalTasks(event.payload?.tasks),
      decision: "",
      comment: "",
    };
    state.activeTab = "timeline";
  }

  if (eventType === "approval_resolved") {
    state.approval = blankApproval();
  }

  if (eventType === "tool_call_finished") {
    state.metrics.toolCalls += 1;
    if (event.payload?.stage_id) {
      attachToolEvidenceToTask(event.payload.stage_id, {
        tool: event.payload.tool || title,
        capabilityId: event.payload.capability_trace?.capability_id || "",
        capabilityName: event.payload.capability_trace?.capability_name || "",
        agent: event.payload.capability_trace?.agent || event.agent || "",
        ok: !String(event.payload.output || content || "").startsWith("Error:"),
        time,
      });
    }
  }

  if (eventType === "task_created" && event.payload?.task) {
    upsertTask(event.payload.task);
    state.rightTab = "tasks";
  }

  if (eventType === "task_updated" && event.payload?.task_id) {
    patchTask(event.payload.task_id, {
      status: event.payload.status,
      title: event.payload.title,
      description: event.payload.description,
      owner: event.payload.owner,
      capabilities: event.payload.capabilities,
    });
    state.rightTab = "tasks";
  }

  if (eventType === "team_updated" && event.payload?.members) {
    state.team = mapBackendTeam(event.payload.members);
    state.rightTab = "team";
  }

  if (eventType === "file_changed" && event.payload?.path) {
    upsertFile(event.payload.path);
  }

  if (eventType === "diff_updated" && event.payload) {
    if (typeof event.payload.diff === "string") {
      setDiffState(
        event.payload.diff || "Diff is empty. The file may be new, unchanged, or outside git tracking.",
        event.payload.changed_files || state.report.changedFiles,
      );
    }
    if (Array.isArray(event.payload.changed_files)) {
      event.payload.changed_files.forEach((file) => upsertFile(file));
      if (event.payload.changed_files.length) {
        state.report.changedFiles = event.payload.changed_files.map((file) =>
          typeof file === "string" ? file : file.path,
        );
      }
    }
    state.activeTab = "diff";
  }

  if (eventType === "metrics_updated" && event.payload) {
    state.metrics.tokens = event.payload.total_tokens || state.metrics.tokens;
  }

  if (eventType === "test_finished" && event.payload?.status) {
    state.metrics.tests = event.payload.status === "passed" ? "passed" : event.payload.status;
  }

  if (eventType === "preview_started" && event.payload?.preview_url) {
    state.previewUrl = event.payload.preview_url;
    state.activeTab = "preview";
  }

  if (eventType === "report_ready" && event.payload?.markdown) {
    state.report.markdown = event.payload.markdown;
    if (Array.isArray(event.payload.changed_files)) {
      state.report.changedFiles = event.payload.changed_files.map((file) => file.path || file);
    }
    state.activeTab = "report";
  }

  if (eventType === "traceability_ready" && event.payload?.requirements) {
    setTraceability({
      source: "event",
      coverage_rate: event.payload.coverage_rate || 0,
      total_count: event.payload.requirements.length,
      covered_count: event.payload.requirements.filter((item) => item.status === "covered").length,
      partial_count: event.payload.requirements.filter((item) => item.status === "partial").length,
      missing_count: event.payload.requirements.filter((item) => item.status === "missing").length,
      requirements: event.payload.requirements,
    });
    state.activeTab = "report";
  }

  if (eventType === "done") {
    state.status = event.payload?.status || "completed";
    state.showCompletedTasks = false;
    updateCurrentRunStatus(state.status);
    eventSource?.close();
    if (options.hydrateOnDone !== false) {
      hydrateRunArtifacts(state.currentThreadId);
      refreshReplayEvents(state.currentThreadId);
    }
  }

  if (eventType === "error") {
    state.status = "failed";
    updateCurrentRunStatus("failed");
    eventSource?.close();
  }

  if (options.renderAfter !== false) {
    render();
  }
}

function updateCurrentRunStatus(status) {
  const currentRun = state.runs.find((run) => run.id === state.currentThreadId);
  if (currentRun) {
    currentRun.status = status;
  }
  if (state.currentConversationId) {
    const conversation = state.runs.find((run) => run.id === state.currentConversationId);
    if (conversation) {
      conversation.status = status;
      conversation.threadId = state.currentThreadId;
      conversation.time = nowTime();
    }
  }
}

function buildReportText() {
  return [
    "# 交付报告",
    "",
    state.report.summary,
    "",
    "## 验收点",
    ...state.report.requirements.map((item) => `- ${item}`),
    "",
    "## 需求追踪",
    ...(state.report.traceability?.requirements || []).map(
      (item) => `- ${item.id} ${item.title}: ${traceabilityStatusLabel(item.status)}`,
    ),
    "",
    "## 变更文件",
    ...state.report.changedFiles.map((item) => `- ${item}`),
    "",
    "## 风险和下一步",
    ...state.report.risks.map((item) => `- ${item}`),
  ].join("\n");
}

render();
loadWorkspaceState().finally(() => {
  loadWorkspaceOverview();
  loadRunHistory();
  loadCapabilities();
  loadBenchmarks();
  loadMemoryProfile();
  loadRecoveryCenter();
  refreshWorkspaceData({ allowEmpty: false }).catch(() => {
    render();
  });
});
