export function buildCommandItems() {
  return [
    {
      id: "new-session",
      title: "新建会话",
      description: "在当前项目中开启一个新的 nanoCursor 会话。",
      shortcut: "N",
      section: "工作流",
    },
    {
      id: "open-workspace",
      title: "打开项目目录",
      description: "展开项目路径输入框，切换 nanoCursor 工作区。",
      shortcut: "O",
      section: "工作区",
    },
    {
      id: "sync",
      title: "同步当前项目",
      description: "刷新运行、文件、能力和项目概览。",
      shortcut: "S",
      section: "工作区",
    },
    {
      id: "capabilities",
      title: "打开能力中心",
      description: "管理 Skills、MCP 预设和外部能力。",
      shortcut: "C",
      section: "上下文",
    },
    {
      id: "mcp",
      title: "配置 MCP",
      description: "跳转到能力中心并展开 MCP 相关入口。",
      shortcut: "M",
      section: "上下文",
    },
    {
      id: "report",
      title: "查看交付报告",
      description: "打开底部证据区并切换到报告。",
      shortcut: "R",
      section: "证据",
    },
    {
      id: "diff",
      title: "查看 Diff",
      description: "打开底部证据区并切换到代码变更。",
      shortcut: "D",
      section: "证据",
    },
    {
      id: "timeline",
      title: "查看事件时间线",
      description: "打开运行事件与回放控制。",
      shortcut: "T",
      section: "证据",
    },
    {
      id: "layout-focus",
      title: "切换 Focus 布局",
      description: "只保留主对话和输入框，适合专注输入。",
      shortcut: "F",
      section: "布局",
    },
    {
      id: "layout-workbench",
      title: "切换 Workbench 布局",
      description: "恢复左侧会话、主对话、右侧上下文的默认工作台。",
      shortcut: "W",
      section: "布局",
    },
    {
      id: "layout-review",
      title: "切换 Review 布局",
      description: "展开证据区，适合查看报告和 Diff。",
      shortcut: "V",
      section: "布局",
    },
  ];
}

export function filterCommandItems(query) {
  const normalized = String(query || "").trim().toLowerCase();
  return buildCommandItems().filter((item) => {
    if (!normalized) return true;
    return [item.title, item.description, item.section, item.shortcut]
      .join(" ")
      .toLowerCase()
      .includes(normalized);
  });
}
