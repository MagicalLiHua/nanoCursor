import React from "react";

const TOOL_ICONS = {
  read_file: "READ",
  write_file: "WRITE",
  edit_file: "EDIT",
  list_directory: "LIST",
  bash: "SH",
  search_codebase: "SEARCH",
  run_tests: "TEST",
  spawn_agent: "SPAWN",
  gather_agents: "MERGE",
  project_context: "CTX",
  task_create: "TASK",
  task_update: "TASK",
};

const TOOL_LABELS = {
  read_file: "读取",
  write_file: "写入",
  edit_file: "编辑",
  list_directory: "列出目录",
  bash: "执行命令",
  search_codebase: "搜索",
  run_tests: "运行测试",
  spawn_agent: "创建 Agent",
  gather_agents: "收集结果",
  project_context: "查看项目",
  task_create: "创建任务",
  task_update: "更新任务",
};

function extractTarget(tool, payload) {
  if (tool === "read_file" || tool === "write_file" || tool === "edit_file") {
    return payload?.input?.path || payload?.path || "";
  }
  if (tool === "bash") {
    return payload?.input?.command || payload?.command || "";
  }
  if (tool === "search_codebase") {
    return payload?.input?.query || payload?.query || "";
  }
  if (tool === "run_tests") {
    return payload?.input?.test_path || payload?.test_path || "";
  }
  return "";
}

function extractPreview(output) {
  if (!output) return "";
  const lines = String(output).split("\n").filter(Boolean);
  return lines[0]?.slice(0, 80) || "";
}

export default function ToolCallBubble({ event }) {
  const payload = event.payload || {};
  const tool = payload.tool || "";
  const agent = payload.capability_trace?.agent || event.agent || "Agent";
  const icon = TOOL_ICONS[tool] || "TOOL";
  const label = TOOL_LABELS[tool] || tool;
  const target = extractTarget(tool, payload);
  const preview = extractPreview(payload.output);
  const ok = !String(payload.output || "").startsWith("Error:");

  return (
    <div className={`tool-call-bubble ${ok ? "success" : "error"}`}>
      <div className="tool-call-agent">
        <span>{icon}</span>
        <span>{agent}</span>
      </div>
      <div className="tool-call-detail">
        <span className="tool-call-action">{label}</span>
        {target && <span className="tool-call-target">{target}</span>}
        {preview && <span className="tool-call-preview">{preview}</span>}
      </div>
      <span className={`tool-call-status ${ok ? "ok" : "err"}`}>
        {ok ? "✓" : "✗"}
      </span>
    </div>
  );
}
