export function bindComposerEvents(context) {
  const {
    state,
    recommendationMutedKey,
    render,
    withBusyAction,
    scheduleCapabilityRecommendation,
    resizePromptInput,
    runPrompt,
    createCustomAgent,
    refreshConversationTeam,
    removeTeamMember,
    createPreference,
    getRecommendationRenderDeferred,
    setRecommendationRenderDeferred,
  } = context;

  document.querySelector("[data-action='dismiss-recommendation']")?.addEventListener("click", () => {
    state.capabilityRecommendationDismissed = true;
    state.capabilityRecommendationMuted = true;
    sessionStorage.setItem(recommendationMutedKey, "1");
    render();
  });

  document.querySelector("[data-action='toggle-recommendation-detail']")?.addEventListener("click", () => {
    state.ui = state.ui || { busyActions: {}, toast: null, workspacePickerOpen: false, recommendationExpanded: false };
    state.ui.recommendationExpanded = !state.ui.recommendationExpanded;
    render();
  });

  document.querySelector("[data-action='toggle-completed-tasks']")?.addEventListener("click", () => {
    state.showCompletedTasks = !state.showCompletedTasks;
    render();
  });

  document.querySelector("#prompt-input")?.addEventListener("input", (event) => {
    state.prompt = event.target.value;
    resizePromptInput(event.target);
    if (!state.capabilityRecommendationMuted) {
      state.capabilityRecommendationDismissed = false;
    }
    scheduleCapabilityRecommendation(state.prompt);
  });

  document.querySelector("#prompt-input")?.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      document.querySelector("#prompt-form")?.requestSubmit();
    }
  });

  document.querySelector("#prompt-input")?.addEventListener("blur", () => {
    if (getRecommendationRenderDeferred()) {
      setRecommendationRenderDeferred(false);
      render();
    }
  });

  document.querySelector("#prompt-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    setRecommendationRenderDeferred(false);
    const input = document.querySelector("#prompt-input");
    const prompt = input.value.trim();
    if (prompt) {
      withBusyAction("run-prompt", async () => runPrompt(prompt));
    }
  });

  document.querySelector("#agent-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await createCustomAgent();
  });

  document.querySelector("[data-action='refresh-conversation-team']")?.addEventListener("click", async () => {
    await refreshConversationTeam(state.prompt);
  });

  document.querySelectorAll("[data-action='remove-team-member']").forEach((button) => {
    button.addEventListener("click", async () => {
      await removeTeamMember(Number(button.dataset.index));
    });
  });

  document.querySelector("#preference-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await createPreference();
  });
}
