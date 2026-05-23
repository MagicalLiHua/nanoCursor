export function normalizeCapabilityRecommendation(result) {
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

export function inferLocalCapabilityRecommendation(prompt, context) {
  const { capabilityHub, getCapabilityOptions, capabilityDisplayName } = context;
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
  const capabilities = uniqueItems(activeRules.flatMap((rule) => rule.capabilityIds)).map((capabilityId) =>
    resolveCapabilityById(capabilityId, { capabilityHub, getCapabilityOptions, capabilityDisplayName }),
  );
  return normalizeCapabilityRecommendation({
    agents,
    capabilities,
    reasons: activeRules.map((rule) => rule.reason).slice(0, 3),
  });
}

function resolveCapabilityById(capabilityId, { capabilityHub, getCapabilityOptions, capabilityDisplayName }) {
  const capability = (capabilityHub?.capabilities || getCapabilityOptions()).find((item) => item.id === capabilityId);
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
