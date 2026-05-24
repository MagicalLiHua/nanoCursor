export function createRecommendationController({
  state,
  render,
  requestJson,
  mutedStorageKey,
  recommendCapabilities,
  normalizeCapabilityRecommendation,
  inferLocalCapabilityRecommendation,
  getPrompt = () => state.prompt,
}) {
  let recommendationTimer = null;
  let renderDeferred = false;

  function isMuted() {
    return Boolean(state.capabilityRecommendationMuted);
  }

  function dismissRecommendation() {
    state.capabilityRecommendationDismissed = true;
    state.capabilityRecommendationMuted = true;
    sessionStorage.setItem(mutedStorageKey, "1");
    render();
  }

  function toggleRecommendationDetail() {
    state.ui = state.ui || {
      busyActions: {},
      toast: null,
      workspacePickerOpen: false,
      recommendationExpanded: false,
    };
    state.ui.recommendationExpanded = !state.ui.recommendationExpanded;
    render();
  }

  function markRecommendationTyping() {
    if (!isMuted()) {
      state.capabilityRecommendationDismissed = false;
    }
    scheduleCapabilityRecommendation(getPrompt());
  }

  function scheduleCapabilityRecommendation(prompt) {
    if (isMuted()) return;
    window.clearTimeout(recommendationTimer);
    recommendationTimer = window.setTimeout(() => {
      refreshCapabilityRecommendation(prompt);
    }, 350);
  }

  async function refreshCapabilityRecommendation(prompt) {
    const text = String(prompt || "").trim();
    if (!text) return;
    if (text !== String(getPrompt() || "").trim()) return;
    try {
      const result = await recommendCapabilities({ requestJson, prompt: text });
      if (text !== String(getPrompt() || "").trim()) return;
      state.capabilityRecommendation = normalizeCapabilityRecommendation(result);
    } catch {
      if (text !== String(getPrompt() || "").trim()) return;
      state.capabilityRecommendation = inferLocalCapabilityRecommendation(text);
    }
    if (document.activeElement?.id === "prompt-input") {
      renderDeferred = true;
      return;
    }
    render();
  }

  return {
    dismissRecommendation,
    getRecommendationRenderDeferred: () => renderDeferred,
    inferLocalCapabilityRecommendation,
    markRecommendationTyping,
    refreshCapabilityRecommendation,
    scheduleCapabilityRecommendation,
    setRecommendationRenderDeferred: (value) => {
      renderDeferred = value;
    },
    toggleRecommendationDetail,
  };
}
