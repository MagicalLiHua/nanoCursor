const STATE_EVENT_TYPES = new Set([
  "approval_resolved",
  "tool_approval_required",
  "run_waiting_approval",
  "stage_updated",
  "task_created",
  "task_updated",
  "team_updated",
  "tool_call_finished",
  "file_changed",
  "diff_updated",
  "test_finished",
  "preview_started",
  "report_ready",
  "traceability_ready",
  "done",
  "error",
]);

export function createReplayController({
  state,
  createBlankReplay,
  render,
  resetRunView,
  hydrateRunArtifacts,
  handleAgentEvent,
}) {
  let replayTimer = null;

  function stopReplayTimer() {
    if (replayTimer) {
      window.clearTimeout(replayTimer);
      replayTimer = null;
    }
  }

  function setReplayEvents(events = [], { prompt = state.prompt, startedAt = "" } = {}) {
    state.replay.events = events;
    state.replay.index = events.length;
    state.replay.status = events.length ? "ready" : "idle";
    state.replay.prompt = prompt || "";
    state.replay.startedAt = startedAt || "";
  }

  function clearReplayState() {
    stopReplayTimer();
    state.replay = createBlankReplay();
  }

  function applyRunStateSnapshot(events = []) {
    events
      .filter((event) => STATE_EVENT_TYPES.has(event.type))
      .forEach((event) => {
        handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false, focusPanel: false });
      });
  }

  function resetReplayToStart() {
    if (!state.replay.events.length) return;
    stopReplayTimer();
    state.replay.index = 0;
    state.replay.status = "paused";
    state.status = "replay_paused";
    state.activeTab = "timeline";
    resetRunView(state.replay.prompt || state.prompt);
    if (state.messages[0] && state.replay.startedAt) {
      state.messages[0].time = state.replay.startedAt;
    }
    render();
  }

  function setReplaySpeed(speed) {
    state.replay.speed = speed;
    if (state.replay.status === "playing") {
      stopReplayTimer();
      scheduleReplayStep();
    } else {
      render();
    }
  }

  function startReplay() {
    if (!state.replay.events.length) return;
    if (state.replay.index >= state.replay.events.length) {
      resetReplayToStart();
    }
    state.replay.status = "playing";
    state.status = "replaying";
    state.activeTab = "timeline";
    render();
    scheduleReplayStep();
  }

  function pauseReplay() {
    stopReplayTimer();
    if (state.replay.events.length) {
      state.replay.status = "paused";
      state.status = "replay_paused";
    }
    render();
  }

  function scheduleReplayStep() {
    stopReplayTimer();
    if (state.replay.status !== "playing") return;
    const delay = Math.max(120, 900 / Number(state.replay.speed || 1));
    replayTimer = window.setTimeout(() => {
      replayTimer = null;
      applyReplayStep();
    }, delay);
  }

  async function finishReplay() {
    stopReplayTimer();
    state.replay.status = "finished";
    state.replay.index = state.replay.events.length;
    if (state.status === "replaying") {
      state.status = "completed";
    }
    await hydrateRunArtifacts(state.currentThreadId, { refreshWorkspace: false });
    render();
  }

  function applyReplayStep() {
    if (state.replay.status !== "playing") return;
    const event = state.replay.events[state.replay.index];
    if (!event) {
      finishReplay();
      return;
    }

    handleAgentEvent(event, { renderAfter: false, hydrateOnDone: false });
    state.replay.index += 1;

    if (state.replay.index >= state.replay.events.length) {
      finishReplay();
      return;
    }

    render();
    scheduleReplayStep();
  }

  return {
    applyRunStateSnapshot,
    clearReplayState,
    pauseReplay,
    resetReplayToStart,
    setReplayEvents,
    setReplaySpeed,
    startReplay,
    stopReplayTimer,
  };
}
