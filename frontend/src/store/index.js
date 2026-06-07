import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  STORAGE_KEYS,
  getStorageValue,
  loadLayoutPreference,
} from "../core/storage.js";
import {
  blankArtifactCenter,
  blankRecoveryCenter,
  blankReport,
} from "../state/runDefaults.js";
import { createLayoutActions } from "./actions/layoutActions.js";
import { createToastActions } from "./actions/toastActions.js";
import { createBusyActions } from "./actions/busyActions.js";
import { createEventActions } from "./actions/eventActions.js";
import { createWorkspaceActions } from "./actions/workspaceActions.js";
import { createRunActions } from "./actions/runActions.js";

const RECOMMENDATION_MUTED_KEY = STORAGE_KEYS.recommendationMuted;

const useStore = create(
  devtools(
    (set, get) => ({
      // --- Run lifecycle ---
      status: "idle",
      activeTab: "report",
      leftTab: "sessions",
      rightTab: "progress",
      runStartedAt: null,
      currentThreadId: "pending",
      currentConversationId: "",
      runSnapshot: null,
      currentRunStrategy: "",

      // --- Workspace ---
      workspaceDir: getStorageValue("workspaceDir") || "",
      workspaceInput: getStorageValue("workspaceDir") || "",
      projectOverview: {
        workspace_dir: getStorageValue("workspaceDir") || "",
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
          entry_points: ["src/api/server.py", "frontend/src/main.js"],
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
      workspaceMeta: {
        default_workspace: "",
        workspace_root: "",
        project_root: "",
        is_default_workspace: false,
      },
      runtimeStatus: {
        indexer: null,
        filetools: null,
      },

      // --- UI ---
      ui: {
        busyActions: {},
        toast: null,
        workspacePickerOpen: false,
        recommendationExpanded: false,
        commandPaletteOpen: false,
        commandQuery: "",
        layoutMode: getStorageValue("layoutMode") || "workbench",
        settingsOpen: false,
        settingsSection: "llm",
      },
      layout: loadLayoutPreference(),

      // --- Prompt ---
      prompt: "",

      // --- Session ---
      runs: [],
      conversations: [],
      messages: [],
      streamingContent: "",
      tasks: [],
      team: [
        {
          name: "Lead",
          role: "lead",
          status: "idle",
          initials: "L",
          tone: "lead",
          goal: "判断任务需要哪些 Agent，并协调临时或永久成员完成交付。",
          tools: ["plan", "delegate", "spawn_agent", "report"],
          capabilities: ["tool.memory", "tool.project_index"],
          lastAction: "等待用户输入；必要时会创建子 Agent。",
          artifacts: ["report", "score"],
        },
      ],

      // --- Events ---
      events: [],
      agentActivities: [],
      agentTokenCounts: {},
      runOutcome: null,
      files: [],
      workspaceFiles: [], // Full file objects with is_dir, path, size, mtime
      metrics: {
        tasks: 0,
        files: 0,
        toolCalls: 0,
        tokens: "--",
        tests: "--",
      },

      // --- Evidence ---
      diff: "",
      diffFiles: [],
      selectedDiffFile: "",
      report: blankReport(),
      artifactCenter: blankArtifactCenter("idle"),
      recoveryCenter: blankRecoveryCenter("safe"),
      previewUrl: "",

      // --- Capability ---
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
                use_cases: ["交付前验收", "风险复盘", "展示用例质量检查"],
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
      capabilityRecommendationMuted:
        sessionStorage.getItem(RECOMMENDATION_MUTED_KEY) === "1",
      showCompletedTasks: false,
      ephemeralAgents: {
        status: "idle",
        suggestions: [],
        agents: [],
        active_count: 0,
        archived_count: 0,
        total: 0,
        includeArchived: false,
        limits: {
          max_active_agents: 3,
          max_suggested_agents: 5,
        },
        mcp_plan_count: 0,
        error: "",
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
      previewUrl: "",
      selectedDiffFile: "",
      diffFiles: [],
      diff: "",
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
      mcpConfig: {
        servers: [],
        presets: [],
        config_paths: [],
        summary: {},
        presetSummary: {},
        validation: null,
        validationByServer: {},
        statusByServer: {},
        toolsByServer: {},
      },
      skillDetail: null,
      skillEditing: false,
      workspaceSettings: null,
      recentProjects: [],

      // --- Replay ---
      replay: {
        events: [],
        index: 0,
        speed: 1,
        status: "idle",
        prompt: "",
        startedAt: "",
      },

      // --- Approval ---
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
      approvalComment: "",

      // --- Internal ---
      _seenEventIds: [],

      // --- Actions ---
      setState: (partial) => set(partial, false),
      getState: get,

      // Layout actions
      ...createLayoutActions(set, get),

      // Toast actions
      ...createToastActions(set, get),

      // Busy actions
      ...createBusyActions(set, get),

      // Event actions
      ...createEventActions(set, get),

      // Workspace actions
      ...createWorkspaceActions(set, get),

      // Run actions
      ...createRunActions(set, get),
    }),
    { name: "nanocursor" }
  )
);

export default useStore;
