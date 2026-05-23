let ephemeralDelegatedEventsBound = false;

export function bindEphemeralDelegatedEvents(context) {
  const {
    state,
    blankEphemeralAgents,
    suggestEphemeralAgents,
    refreshEphemeralAgents,
    spawnEphemeralAgent,
    completeEphemeralAgent,
    archiveEphemeralAgent,
  } = context;

  if (ephemeralDelegatedEventsBound) return;
  document.addEventListener("click", async (event) => {
    const button = event.target?.closest?.("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (![
      "suggest-ephemeral-agents",
      "refresh-ephemeral-agents",
      "toggle-archived-ephemeral",
      "spawn-ephemeral-agent",
      "complete-ephemeral-agent",
      "archive-ephemeral-agent",
    ].includes(action)) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();

    if (action === "suggest-ephemeral-agents") {
      await suggestEphemeralAgents();
    } else if (action === "refresh-ephemeral-agents") {
      await refreshEphemeralAgents({ includeArchived: state.ephemeralAgents?.includeArchived });
    } else if (action === "toggle-archived-ephemeral") {
      state.ephemeralAgents = {
        ...(state.ephemeralAgents || blankEphemeralAgents()),
        includeArchived: !state.ephemeralAgents?.includeArchived,
      };
      await refreshEphemeralAgents({ includeArchived: state.ephemeralAgents.includeArchived });
    } else if (action === "spawn-ephemeral-agent") {
      await spawnEphemeralAgent(Number(button.dataset.index));
    } else if (action === "complete-ephemeral-agent") {
      await completeEphemeralAgent(button.dataset.agentId);
    } else if (action === "archive-ephemeral-agent") {
      await archiveEphemeralAgent(button.dataset.agentId);
    }
  });
  ephemeralDelegatedEventsBound = true;
}
