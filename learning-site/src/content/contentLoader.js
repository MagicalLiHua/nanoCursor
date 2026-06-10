import { parseFrontmatter } from "./frontmatter.js";

const modules = import.meta.glob("./handbook/**/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});

const GROUPS = {
  chapters: { label: "深度章节", order: 1 },
  maps: { label: "代码地图", order: 2 },
  exercises: { label: "动手练习", order: 3 },
  interview: { label: "面试准备", order: 4 },
};

const ROADMAP_NOTES = {
  "01-project-overview": "建立项目定位和整体架构感。",
  "02-request-lifecycle": "跟踪一次真实请求从前端到后端完成。",
  "03-agent-loop": "理解持续决策、工具调用和停止条件。",
  "04-agent-orchestration": "理解 Lead 如何创建和收束子 Agent。",
  "05-context-management": "理解 Context Pack 如何选取相关信息。",
  "06-memory-system": "理解会话摘要、偏好记忆和长期上下文。",
  "07-tool-governance": "理解权限分级、审批和恢复边界。",
  "08-event-store-and-sse": "理解事件流、快照和前端运行感知。",
  "09-runtime-and-async-boundary": "理解异步正确性和阻塞隔离。",
  "10-go-sidecar": "理解 Python 与 Go sidecar 的工程分工。",
  "11-mcp-and-skills": "理解 MCP/Skills 如何进入 Agent 能力系统。",
  "12-frontend-observability": "理解前端如何展示 Agent 正在做什么。",
  "13-testing-and-quality": "理解测试、评估和质量门禁。",
  "14-deployment-and-startup": "理解本地启动、配置和运行边界。",
  "15-project-retrospective": "复盘项目价值、限制和面试表达。",
};

function normalizePath(path) {
  return path.replace(/^.*content\/handbook\//, "").replace(/^\.\/handbook\//, "");
}

function slugify(text = "") {
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[`*_~()[\]{}:：，。,.!?/\\|]+/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function titleFromMarkdown(markdown = "", fallback = "") {
  const heading = markdown.match(/^#\s+(.+)$/m);
  if (heading) return heading[1].trim();
  return fallback.replace(/^\d+-/, "").replace(/-/g, " ");
}

function orderFromFile(file = "") {
  const match = file.match(/^(\d+)/);
  return match ? Number(match[1]) : 999;
}

export function extractHeadings(markdown = "") {
  return markdown
    .split(/\r?\n/)
    .map((line) => line.match(/^(#{1,4})\s+(.+)$/))
    .filter(Boolean)
    .map((match) => {
      const text = match[2].replace(/#+$/, "").trim();
      return {
        level: match[1].length,
        text,
        id: slugify(text),
      };
    });
}

export function extractSourceRefs(markdown = "") {
  const refs = new Set();
  const pattern = /`((?:src|frontend|go-services|scripts|tests)\/[^`\s]+)`/g;
  let match;
  while ((match = pattern.exec(markdown))) {
    refs.add(match[1].replace(/[),.;:]+$/, ""));
  }
  return Array.from(refs).slice(0, 10);
}

export function extractLearningPoints(markdown = "") {
  const lines = markdown.split(/\r?\n/);
  const points = [];
  let inGoalSection = false;
  for (const line of lines) {
    if (/^##\s+\d+\.\s*本章目标/.test(line)) {
      inGoalSection = true;
      continue;
    }
    if (inGoalSection && /^##\s+/.test(line)) break;
    if (!inGoalSection) continue;
    const match = line.match(/^\s*[-*]\s+(.+)$/);
    if (match) points.push(match[1].trim());
  }
  return points.slice(0, 5);
}

export function extractOpeningSummary(markdown = "") {
  const withoutCode = markdown.replace(/```[\s\S]*?```/g, "");
  const paragraphs = withoutCode
    .split(/\n{2,}/)
    .map((item) => item.trim())
    .filter((item) => item && !item.startsWith("#") && !item.startsWith("最后更新") && !item.startsWith("- "));
  return (paragraphs[0] || "").replace(/\s+/g, " ").slice(0, 180);
}

export function loadDocuments() {
  return Object.entries(modules)
    .map(([path, raw]) => {
      const relativePath = normalizePath(path);
      const parts = relativePath.split("/");
      const group = parts[0];
      const fileName = parts.at(-1) || "";
      const id = relativePath.replace(/\.md$/, "");
      const slug = fileName.replace(/\.md$/, "");
      const { attributes, body } = parseFrontmatter(raw);
      const title = attributes.title || titleFromMarkdown(body, slug);
      const headings = extractHeadings(body);
      return {
        id,
        slug,
        group,
        groupLabel: GROUPS[group]?.label || group,
        groupOrder: GROUPS[group]?.order || 99,
        order: Number(attributes.order) || orderFromFile(fileName),
        fileName,
        path: relativePath,
        title,
        body,
        headings,
        sourceRefs: extractSourceRefs(body),
        learningPoints: extractLearningPoints(body),
        openingSummary: extractOpeningSummary(body),
        difficulty: attributes.difficulty || inferDifficulty(group, slug),
        estimatedMinutes: Number(attributes.estimatedMinutes) || inferMinutes(body),
        learningGoal: attributes.learningGoal || ROADMAP_NOTES[slug] || "阅读并关联到真实代码。",
      };
    })
    .filter((doc) => GROUPS[doc.group])
    .sort((a, b) => a.groupOrder - b.groupOrder || a.order - b.order || a.title.localeCompare(b.title));
}

function inferMinutes(markdown = "") {
  const words = markdown.replace(/```[\s\S]*?```/g, "").length;
  return Math.max(8, Math.min(90, Math.round(words / 35)));
}

function inferDifficulty(group, slug) {
  if (group === "maps") return "reference";
  if (group === "exercises") return "practice";
  if (group === "interview") return "review";
  if (["03-agent-loop", "05-context-management", "07-tool-governance", "10-go-sidecar"].includes(slug)) {
    return "advanced";
  }
  return "core";
}

export function firstChapter(documents) {
  return documents.find((doc) => doc.group === "chapters") || documents[0];
}

export function documentRoute(doc) {
  return `#/${doc.id}`;
}

export function routeToId(hash = "") {
  const clean = hash.replace(/^#\/?/, "");
  return clean || "";
}
