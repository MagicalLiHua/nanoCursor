import React from "react";
import { agentToneFromName } from "../../core/format.js";

const STATUS_DOTS = {
  running: "●",
  working: "●",
  pending: "◐",
  completed: "✓",
  failed: "✗",
  cancelled: "○",
  idle: "○",
  archived: "·",
};

const STATUS_CLASSES = {
  running: "status-running",
  working: "status-running",
  pending: "status-pending",
  completed: "status-completed",
  failed: "status-failed",
  cancelled: "status-cancelled",
  idle: "status-idle",
  archived: "status-archived",
};

function AgentDot({ name, status, lastAction }) {
  const tone = agentToneFromName(name);
  const dot = STATUS_DOTS[status] || "○";
  const statusClass = STATUS_CLASSES[status] || "status-idle";

  return (
    <div className={`agent-dot ${tone} ${statusClass}`} title={`${name}: ${lastAction || status}`}>
      <span className="agent-dot-indicator">{dot}</span>
      <span className="agent-dot-name">{name}</span>
    </div>
  );
}

export default function AgentStatusBar({ team = [], ephemeralAgents = [], running = false }) {
  if (!running && team.length === 0) return null;

  const allAgents = [
    ...team.map((m) => ({
      name: m.name || m.role,
      status: m.status || "idle",
      lastAction: m.lastAction,
    })),
    ...ephemeralAgents
      .filter((a) => !["archived", "expired"].includes(a.status))
      .map((a) => ({
        name: a.name || a.role,
        status: a.status || "active",
        lastAction: a.goal,
      })),
  ];

  if (allAgents.length === 0) return null;

  return (
    <div className="agent-status-bar">
      {allAgents.map((agent, i) => (
        <AgentDot key={`${agent.name}-${i}`} {...agent} />
      ))}
    </div>
  );
}
