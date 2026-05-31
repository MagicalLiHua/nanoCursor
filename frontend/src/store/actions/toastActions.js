export function createToastActions(set, get) {
  let toastTimer = null;

  function showToast({ title = "", content = "", kind = "info", duration = 3000 } = {}) {
    if (toastTimer) clearTimeout(toastTimer);
    set((state) => ({ ui: { ...state.ui, toast: { title, content, kind } } }));
    if (duration > 0) {
      toastTimer = setTimeout(() => {
        set((state) => ({ ui: { ...state.ui, toast: null } }));
        toastTimer = null;
      }, duration);
    }
  }

  function clearToast() {
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = null;
    set((state) => ({ ui: { ...state.ui, toast: null } }));
  }

  return { showToast, clearToast };
}
