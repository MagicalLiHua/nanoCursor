export function createBusyController({ state, ensureUiState, render, showToast }) {
  function isActionBusy(action) {
    return Boolean(state.ui?.busyActions?.[action]);
  }

  function setActionBusy(action, busy) {
    ensureUiState();
    state.ui.busyActions = state.ui.busyActions || {};
    if (busy) {
      state.ui.busyActions[action] = true;
    } else {
      delete state.ui.busyActions[action];
    }
  }

  async function withBusyAction(action, callback) {
    if (isActionBusy(action)) return undefined;
    setActionBusy(action, true);
    render();
    try {
      return await callback();
    } catch (error) {
      showToast("error", "操作失败", error.message || String(error));
      return undefined;
    } finally {
      setActionBusy(action, false);
      render();
    }
  }

  return {
    isActionBusy,
    setActionBusy,
    withBusyAction,
  };
}
