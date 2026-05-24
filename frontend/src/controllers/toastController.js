export function createToastController({ state, ensureUiState, render, renderToastView }) {
  let toastTimer = null;

  function showToast(kind, title, content = "", duration = 2600) {
    ensureUiState();
    state.ui.toast = {
      kind,
      title,
      content,
      id: Date.now(),
    };
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      state.ui.toast = null;
      render();
    }, duration);
    render();
  }

  function renderToast() {
    return renderToastView(state.ui?.toast);
  }

  function clearToastTimer() {
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
  }

  return {
    clearToastTimer,
    renderToast,
    showToast,
  };
}
