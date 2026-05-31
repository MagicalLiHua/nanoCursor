export function createBusyActions(set, get) {
  function isActionBusy(actionId) {
    const state = get();
    return Boolean(state.ui?.busyActions?.[actionId]);
  }

  function setActionBusy(actionId, busy) {
    set((state) => ({
      ui: {
        ...state.ui,
        busyActions: { ...state.ui.busyActions, [actionId]: busy },
      },
    }));
  }

  async function withBusyAction(actionId, fn) {
    setActionBusy(actionId, true);
    try {
      return await fn();
    } finally {
      setActionBusy(actionId, false);
    }
  }

  return { isActionBusy, setActionBusy, withBusyAction };
}
