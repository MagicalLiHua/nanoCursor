import {
  archiveEphemeralAgent as archiveEphemeralAgentAction,
  completeEphemeralAgent as completeEphemeralAgentAction,
  loadEphemeralAgents,
  spawnEphemeralAgent as spawnEphemeralAgentAction,
  suggestEphemeralAgents as suggestEphemeralAgentsAction,
} from "../actions/teamActions.js";
import { normalizeEphemeralAgentsResult } from "../state/runDefaults.js";

export function createEphemeralAgentController({
  state,
  render,
  fetchJson,
  requestJson,
  addTimelineEvent,
  blankEphemeralAgents,
}) {
  function isEphemeralThreadReady() {
    const threadId = String(state.currentThreadId || "");
    return Boolean(threadId && threadId !== "pending");
  }

  function upsertEphemeralAgent(agent, { removeIfHidden = true } = {}) {
    if (!agent?.agent_id) return;
    const panel = state.ephemeralAgents || blankEphemeralAgents();
    const agents = Array.isArray(panel.agents) ? [...panel.agents] : [];
    const index = agents.findIndex((item) => item.agent_id === agent.agent_id);
    const shouldHide = removeIfHidden && !panel.includeArchived && ["archived", "expired"].includes(agent.status);
    if (shouldHide) {
      const wasVisible = index >= 0 && !["archived", "expired"].includes(agents[index]?.status);
      state.ephemeralAgents = normalizeEphemeralAgentsResult({
        ...panel,
        agents: agents.filter((item) => item.agent_id !== agent.agent_id),
        archived_count: Number(panel.archived_count || 0) + (wasVisible ? 1 : 0),
      }, panel);
      return;
    }
    if (index >= 0) {
      agents[index] = { ...agents[index], ...agent };
    } else {
      agents.unshift(agent);
    }
    const activeCount = agents.filter((item) => !["archived", "expired"].includes(item.status)).length;
    state.ephemeralAgents = normalizeEphemeralAgentsResult({
      ...panel,
      agents,
      active_count: activeCount,
      total: agents.length,
    }, panel);
  }

  async function refreshEphemeralAgents({ includeArchived = false, renderAfter = true } = {}) {
    if (!isEphemeralThreadReady()) return;
    const previous = state.ephemeralAgents || blankEphemeralAgents();
    try {
      const result = await loadEphemeralAgents({
        fetchJson,
        threadId: state.currentThreadId,
        includeArchived,
      });
      state.ephemeralAgents = normalizeEphemeralAgentsResult(
        {
          ...result,
          includeArchived,
          suggestions: previous.suggestions || [],
        },
        previous,
      );
    } catch (error) {
      state.ephemeralAgents = {
        ...previous,
        status: "error",
        error: error.message,
      };
    }
    if (renderAfter) render();
  }

  async function suggestEphemeralAgents() {
    if (!isEphemeralThreadReady()) return;
    const previous = state.ephemeralAgents || blankEphemeralAgents();
    state.ephemeralAgents = {
      ...previous,
      status: "loading",
      error: "",
    };
    state.rightTab = "ephemeral";
    render();

    try {
      const result = await suggestEphemeralAgentsAction({
        requestJson,
        threadId: state.currentThreadId,
        prompt: state.prompt,
      });
      state.ephemeralAgents = normalizeEphemeralAgentsResult(
        {
          ...previous,
          ...result,
          agents: previous.agents || [],
          includeArchived: previous.includeArchived,
        },
        previous,
      );
      addTimelineEvent({
        type: "ephemeral_agents_suggested",
        title: "临时子 Agent 建议已生成",
        content: `Lead 推荐 ${result.suggestions?.length || 0} 个任务级子 Agent。`,
      });
    } catch (error) {
      state.ephemeralAgents = {
        ...previous,
        status: "error",
        error: error.message,
      };
      addTimelineEvent({
        type: "error",
        title: "临时子 Agent 建议失败",
        content: error.message,
      });
    }
  }

  async function spawnEphemeralAgent(index) {
    const panel = state.ephemeralAgents || blankEphemeralAgents();
    const suggestion = panel.suggestions?.[index];
    if (!suggestion || !isEphemeralThreadReady()) return;
    try {
      const result = await spawnEphemeralAgentAction({
        requestJson,
        threadId: state.currentThreadId,
        agent: suggestion,
      });
      const nextSuggestions = panel.suggestions.filter((_, itemIndex) => itemIndex !== index);
      state.ephemeralAgents = normalizeEphemeralAgentsResult(
        {
          ...panel,
          suggestions: nextSuggestions,
        },
        panel,
      );
      upsertEphemeralAgent(result.agent, { removeIfHidden: false });
      state.rightTab = "ephemeral";
      addTimelineEvent({
        type: "ephemeral_agent_spawned",
        title: "临时子 Agent 已加入",
        content: `${result.agent?.name || suggestion.name} 将处理本轮任务的独立子问题。`,
        payload: result.agent || suggestion,
      });
      await refreshEphemeralAgents({ includeArchived: panel.includeArchived, renderAfter: false });
    } catch (error) {
      state.ephemeralAgents = {
        ...panel,
        status: "error",
        error: error.message,
      };
      addTimelineEvent({
        type: "error",
        title: "临时子 Agent 加入失败",
        content: error.message,
      });
    }
    render();
  }

  async function completeEphemeralAgent(agentId) {
    if (!agentId || !isEphemeralThreadReady()) return;
    const panel = state.ephemeralAgents || blankEphemeralAgents();
    const agent = panel.agents?.find((item) => item.agent_id === agentId);
    const summary = window.prompt(
      `填写 ${agent?.name || "临时子 Agent"} 的完成摘要`,
      `${agent?.name || "临时子 Agent"} 已完成本轮子任务。`,
    );
    if (summary === null) return;

    try {
      const result = await completeEphemeralAgentAction({
        requestJson,
        threadId: state.currentThreadId,
        agentId,
        summary,
      });
      upsertEphemeralAgent(result.agent);
      await refreshEphemeralAgents({ includeArchived: panel.includeArchived, renderAfter: false });
      addTimelineEvent({
        type: "ephemeral_agent_completed",
        title: "临时子 Agent 已完成",
        content: summary,
        payload: result.agent,
      });
    } catch (error) {
      state.ephemeralAgents = {
        ...panel,
        status: "error",
        error: error.message,
      };
      addTimelineEvent({
        type: "error",
        title: "临时子 Agent 完成失败",
        content: error.message,
      });
    }
    render();
  }

  async function archiveEphemeralAgent(agentId) {
    if (!agentId || !isEphemeralThreadReady()) return;
    const panel = state.ephemeralAgents || blankEphemeralAgents();
    const agent = panel.agents?.find((item) => item.agent_id === agentId);
    const reason = window.prompt(
      `归档 ${agent?.name || "临时子 Agent"} 的原因`,
      "本轮任务不再需要该临时子 Agent。",
    );
    if (reason === null) return;

    try {
      const result = await archiveEphemeralAgentAction({
        requestJson,
        threadId: state.currentThreadId,
        agentId,
        reason,
      });
      upsertEphemeralAgent(result.agent);
      await refreshEphemeralAgents({ includeArchived: panel.includeArchived, renderAfter: false });
      addTimelineEvent({
        type: "ephemeral_agent_archived",
        title: "临时子 Agent 已归档",
        content: reason,
        payload: result.agent,
      });
    } catch (error) {
      state.ephemeralAgents = {
        ...panel,
        status: "error",
        error: error.message,
      };
      addTimelineEvent({
        type: "error",
        title: "临时子 Agent 归档失败",
        content: error.message,
      });
    }
    render();
  }

  return {
    archiveEphemeralAgent,
    completeEphemeralAgent,
    isEphemeralThreadReady,
    refreshEphemeralAgents,
    spawnEphemeralAgent,
    suggestEphemeralAgents,
    upsertEphemeralAgent,
  };
}
