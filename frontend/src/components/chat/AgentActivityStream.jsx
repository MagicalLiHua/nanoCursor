import React from "react";
import { agentToneFromName } from "../../core/format.js";

const ACTIVITY_ICONS = {
  started: "",
  completed: "",
  failed: "",
  cancelled: "",
  working: "",
};

const TOOL_ICONS = {
  read_file: "read",
  write_file: "write",
  edit_file: "edit",
  list_directory: "list",
  bash: "shell",
  search_codebase: "search",
  run_tests: "test",
  spawn_agent: "spawn",
  gather_agents: "merge",
  project_context: "ctx",
  task_create: "task",
  task_update: "task",
};

function ActivityItem({ activity }) {
  const tone = agentToneFromName(activity.agent);
  const letter = String(activity.agent || "A").charAt(0).toUpperCase();
  const isRunning = activity.eventType === "agent_activity" && !activity.text?.includes("已");
  const isToolCall = activity.eventType === "tool_call_finished";
  const toolName = activity.payload?.tool;

  return (
    <div className={`agent-activity-item ${isRunning ? "running" : ""} ${isToolCall ? "tool-call" : ""}`}>
      <div className={`agent-avatar-mini ${tone}`}>
        <span>{letter}</span>
      </div>
      <div className="agent-activity-content">
        <span className="agent-activity-agent">{activity.agent}</span>
        <span className="agent-activity-text">
          {isToolCall && TOOL_ICONS[toolName] && <span className="tool-icon">{TOOL_ICONS[toolName]}</span>}
          {activity.text}
        </span>
      </div>
      <span className="agent-activity-time">{activity.time}</span>
    </div>
  );
}

export default function AgentActivityStream({ activities = [], maxItems = 5, fallback = "" }) {
  const filtered = activities
    .filter((a) => a.explicitAgentWork)
    .filter((a) => a.eventType !== "token" && a.eventType !== "metrics_updated")
    .slice(0, maxItems);

  if (filtered.length === 0 && !fallback) return null;

  return (
    <div className="agent-activity-stream">
      {filtered.length ? (
        filtered.map((activity, i) => (
          <ActivityItem key={`${activity.time}-${i}`} activity={activity} />
        ))
      ) : (
        <ActivityItem activity={{
          agent: "Lead",
          text: fallback,
          eventType: "agent_activity",
          time: "实时",
          explicitAgentWork: true,
        }} />
      )}
    </div>
  );
}
