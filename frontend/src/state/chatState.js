export function latestUserMessageIndex(messages = []) {
  return messages.reduce(
    (latest, message, index) => (message?.role === "user" ? index : latest),
    -1,
  );
}

const TERMINAL_ACTIVITY_EVENTS = new Set([
  "agent_result_merged",
  "agent_run_failed",
  "ephemeral_agent_archived",
  "ephemeral_agent_completed",
  "ephemeral_agent_expired",
  "parallel_agent_failed",
  "parallel_agent_result",
]);

const TERMINAL_ACTIVITY_STATUSES = new Set([
  "archived",
  "cancelled",
  "completed",
  "expired",
  "failed",
]);

export function agentActivityKey(activity = {}) {
  const explicitId = activity.payload?.agent_id ||
    activity.payload?.agent?.agent_id ||
    activity.payload?.capability_trace?.agent_id;
  if (explicitId) return String(explicitId).trim().toLowerCase();

  const key = String(activity.agent || activity.payload?.capability_trace?.agent || "Lead")
    .replace(/\s*Agent$/i, "")
    .trim()
    .toLowerCase();
  const aliases = {
    lead: "lead",
    planner: "planner",
    coder: "coder",
    tester: "tester",
    reviewer: "reviewer",
    designer: "designer",
    devops: "devops",
    security: "security",
    migration: "migration",
    system: "system",
  };
  return aliases[key] || key || "lead";
}

export function isTerminalAgentActivity(activity = {}) {
  if (TERMINAL_ACTIVITY_EVENTS.has(activity.eventType)) return true;
  const payloadStatus = String(activity.payload?.status || activity.status || "").toLowerCase();
  if (TERMINAL_ACTIVITY_STATUSES.has(payloadStatus)) return true;
  const poolEvent = String(activity.payload?.event || "").toLowerCase();
  return TERMINAL_ACTIVITY_STATUSES.has(poolEvent);
}

export function updateAgentActivityQueue(queue = [], activity = {}, { limit = 8 } = {}) {
  const key = agentActivityKey(activity);
  const existingIndex = queue.findIndex((item) => agentActivityKey(item) === key);

  if (isTerminalAgentActivity(activity)) {
    return existingIndex < 0
      ? queue
      : queue.filter((_, index) => index !== existingIndex);
  }

  if (!activity?.explicitAgentWork || !String(activity.text || "").trim()) return queue;
  if (["token", "metrics_updated", "assistant_message"].includes(activity.eventType)) return queue;

  if (existingIndex >= 0) {
    const next = [...queue];
    next[existingIndex] = {
      ...next[existingIndex],
      ...activity,
      queueEnteredAt: next[existingIndex].queueEnteredAt || activity.time || "",
    };
    return next;
  }

  return [
    ...queue,
    {
      ...activity,
      queueEnteredAt: activity.queueEnteredAt || activity.time || "",
    },
  ].slice(-limit);
}

export function buildAgentActivityQueue(activities = [], { initialQueue = [], ...options } = {}) {
  return activities.reduce(
    (queue, activity) => updateAgentActivityQueue(queue, activity, options),
    initialQueue,
  );
}

export function currentAgentActivities(activities = [], { running = false, limit = 6 } = {}) {
  if (!running) return [];
  return activities
    .filter((activity) => !isTerminalAgentActivity(activity))
    .filter((activity) => activity?.explicitAgentWork && String(activity.text || "").trim())
    .filter((activity) => !["token", "metrics_updated", "assistant_message"].includes(activity.eventType))
    .slice(0, limit);
}
